"""Host agents: enrolment, the WebSocket link, signed jobs, revocation, metrics, the offline buffer,
service discovery, patches and Ansible bundles. The agent runs in this process against a real
(local) labdex server; commands on the "host" come from a fake runner, never this machine."""
import io
import json
import os
import tarfile
import threading
import time

import pytest
from werkzeug.serving import make_server

from labdex import agents
from labdex.agent import ansible as agent_ansible
from labdex.agent import core, discovery, jobs, protocol
from labdex.models import Agent, AgentJob, AuditEvent, DiscoveredService, HostPatch, db

DNS = "srv-vm-dns-01"

SS = """\
tcp   LISTEN 0      4096         0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=812,fd=3))
tcp   LISTEN 0      4096            [::]:22            [::]:*    users:(("sshd",pid=812,fd=4))
udp   UNCONN 0      0            0.0.0.0:53         0.0.0.0:*    users:(("dnsd",pid=900,fd=5),("dnsd",pid=900,fd=6))
tcp   LISTEN 0      511        127.0.0.1:6379       0.0.0.0:*    users:(("redis-server",pid=1001,fd=6))
tcp   LISTEN 0      4096         0.0.0.0:5380       0.0.0.0:*    users:(("docker-proxy",pid=1500,fd=4))
tcp   LISTEN 0      4096   127.0.0.53%lo:53         0.0.0.0:*    users:(("systemd-resolve",pid=600,fd=14))
"""
UNITS = "ssh.service loaded active running OpenBSD Secure Shell server\ndnsd.service loaded active running DNS\n"
SHOW = """Id=ssh.service
Description=OpenBSD Secure Shell server
MainPID=812
User=
FragmentPath=/lib/systemd/system/ssh.service
ActiveEnterTimestamp=Mon 2026-11-16 08:00:00 UTC

Id=dnsd.service
Description=DNS
MainPID=900
User=dns
FragmentPath=/etc/systemd/system/dnsd.service
ActiveEnterTimestamp=Mon 2026-11-16 08:00:01 UTC
"""
DOCKER = json.dumps({"ID": "0123456789abcdef", "Names": "technitium", "Image": "technitium/dns-server:13", "Status": "Up 2 days",
                     "Labels": "com.docker.compose.project=dns,other=x", "Ports": "0.0.0.0:5380->5380/tcp, [::]:5380->5380/tcp"})


class FakeRunner(jobs.Runner):
    """The host's commands, canned. Records what it was asked to run."""

    def __init__(self, root=False, programs=("systemctl", "docker", "apt-get")):
        super().__init__(root=root)
        self.programs = set(programs)
        self.calls = []

    def has(self, program):
        return program in self.programs

    def privileged(self, name, timeout=60):
        self.calls.append(name)
        return {"ss": (0, SS, ""), "docker_ps": (0, DOCKER + "\n", ""), "apt_update": (0, "Hit: 1\n", ""),
                "apt_upgrade": (0, "0 upgraded\n", ""), "reboot": (0, "", "")}.get(name, (1, "", "sudo: a password is required"))

    def run(self, argv, timeout=60):
        self.calls.append(argv[0] if argv[0] != "systemctl" else " ".join(argv[:2]))
        if argv[:2] == ["systemctl", "list-units"]:
            return 0, UNITS, ""
        if argv[:2] == ["systemctl", "show"]:
            return 0, SHOW, ""
        return 127, "", "not here"


def fake_proc(tmp_path):
    proc = tmp_path / "proc"
    (proc).mkdir()
    (proc / "uptime").write_text("1000.0 900.0\n")
    procs = {
        812: ("sshd", 1, 0, ["sshd: /usr/sbin/sshd -D [listener]"], "0::/system.slice/ssh.service\n"),
        900: ("dnsd", 1, 107, ["/usr/bin/dnsd", "--admin-password", "hunter2", "--token=abc", "DB_PASS=x",
                               "--upstream", "https://user:pw@dns.example.com/q"], "0::/system.slice/dnsd.service\n"),
        1001: ("redis-server", 1, 108, ["redis-server *:6379"], "0::/system.slice/redis.service\n"),
        1500: ("docker-proxy", 700, 0, ["/usr/bin/docker-proxy", "-proto", "tcp", "-host-port", "5380"], "0::/system.slice/docker.service\n"),
        700: ("dockerd", 1, 0, ["/usr/bin/dockerd", "-H", "fd://"], "0::/system.slice/docker.service\n"),
    }
    for pid, (name, ppid, uid, argv, cgroup) in procs.items():
        d = proc / str(pid)
        d.mkdir()
        (d / "status").write_text(f"Name:\t{name}\nPPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        (d / "cmdline").write_text("\0".join(argv) + "\0")
        (d / "cgroup").write_text(cgroup)
        (d / "stat").write_text(f"{pid} ({name}) S {ppid} " + " ".join(["0"] * 17) + " 5000 0 0\n")
    return str(proc)


# ----------------------------------------------------------------------------- a live hub
@pytest.fixture
def hub(loaded, admin, tmp_path, monkeypatch):
    loaded.config["LABDEX_DATA_DIR"] = str(tmp_path / "data")
    monkeypatch.setattr(protocol, "HEARTBEAT", 1)
    server = make_server("127.0.0.1", 0, loaded, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "admin": admin, "tmp": tmp_path}
    server.shutdown()


def enrol(hub, allow=(), root=False, allow_root=False, name="a1"):
    r = hub["admin"].post(f"/api/hosts/{DNS}/agent/token", json={"allow_root": allow_root})
    assert r.status_code == 201, r.get_json()
    token = r.get_json()["token"]
    assert token.startswith("lxe_") and "install.sh" in r.get_json()["command"] and r.get_json()["sha256"]
    cfg = core.Config("/nonexistent", url=hub["url"], allow=list(allow), state_dir=str(hub["tmp"] / name))
    creds = core.enrol(cfg, token, allow_http=True, root=root)
    return cfg, creds


def start(cfg, creds, runner=None, logs=None):
    a = core.Agent(cfg, creds, runner=runner or FakeRunner(), log=(logs.append if logs is not None else lambda m: None))
    out = {}

    def go():
        try:
            a.run()
        except core.Refused as e:
            out["refused"] = str(e)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return a, th, out


def wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        db.session.expire_all()
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


def job_done(admin, job_id):
    def check():
        return admin.get(f"/api/agents/jobs/{job_id}").get_json()["status"] not in ("queued", "sent", "running")
    wait(check)
    return admin.get(f"/api/agents/jobs/{job_id}").get_json()


# ----------------------------------------------------------------------------- enrolment
def test_enrol_once_and_only_once(hub):
    cfg, creds = enrol(hub)
    assert creds["host"] == DNS and creds["key"].startswith("lxa_") and creds["signing_key"]
    stat = os.stat(os.path.join(cfg.state_dir, "credentials.json"))
    assert stat.st_mode & 0o077 == 0
    a = db.session.get(Agent, creds["agent_id"])
    assert a.key_hash != creds["key"] and creds["key"].split("_", 2)[2] not in a.key_hash  # only a hash is kept
    assert agents.authenticate(creds["key"]) is a and agents.authenticate(creds["key"] + "x") is None
    # the token is spent
    c = hub["admin"]
    r = c.post("/api/agents/enrol", json={"token": "lxe_nope"})
    assert r.status_code == 403
    tok = c.post(f"/api/hosts/{DNS}/agent/token", json={}).get_json()["token"]
    assert c.post("/api/agents/enrol", json={"token": tok}).status_code == 200
    assert c.post("/api/agents/enrol", json={"token": tok}).status_code == 403
    # enrolling again revoked the first agent
    db.session.expire_all()
    assert db.session.get(Agent, creds["agent_id"]).revoked_at is not None
    assert {e.action for e in db.session.scalars(db.select(AuditEvent).where(AuditEvent.object_type == "agent"))} >= {"enrol-token", "enrol", "revoke"}


def test_enrol_endpoints_need_no_login_but_tokens_do(hub, loaded):
    from .conftest import client
    anon = client(loaded)
    assert anon.post(f"/api/hosts/{DNS}/agent/token", json={}).status_code == 401
    assert anon.get("/api/agents/install.sh").status_code == 200
    pyz = anon.get("/api/agents/download/labdex-agent.pyz")
    assert pyz.status_code == 200
    import hashlib
    import zipfile
    assert hashlib.sha256(pyz.data).hexdigest() == pyz.headers["X-Labdex-SHA256"]
    names = zipfile.ZipFile(io.BytesIO(pyz.data)).namelist()
    assert "__main__.py" in names and "labdex_agent/core.py" in names and "labdex_agent/protocol.py" in names
    assert anon.get("/api/agents/download/labdex-agent.service").status_code == 200
    assert anon.get("/api/agents/download/secret.key").status_code == 404


def test_a_viewer_cant_make_tokens_and_only_admins_allow_root(hub, make_user):
    viewer = make_user("vic", role="viewer")
    assert viewer.post(f"/api/hosts/{DNS}/agent/token", json={}).status_code == 403
    editor = make_user("eddie", role="editor")
    assert editor.post(f"/api/hosts/{DNS}/agent/token", json={}).status_code == 201
    assert editor.post(f"/api/hosts/{DNS}/agent/token", json={"allow_root": True}).status_code == 403


def test_root_agent_needs_the_hosts_opt_in(hub):
    with pytest.raises(SystemExit, match="root"):
        enrol(hub, root=True)
    cfg, creds = enrol(hub, root=True, allow_root=True)
    assert db.session.get(Agent, creds["agent_id"]).allow_root
    # an administrator takes root away again: the root agent can't link any more
    hub["admin"].patch(f"/api/hosts/{DNS}/agent", json={"allow_root": False})
    agent, th, out = start(cfg, creds, runner=FakeRunner(root=True))
    th.join(10)
    assert "4403" in out["refused"]


# ----------------------------------------------------------------------------- link and jobs
def test_link_ping_heartbeat_metrics_and_audit(hub):
    cfg, creds = enrol(hub)
    agent, th, out = start(cfg, creds)
    a_id = creds["agent_id"]
    wait(lambda: agents.linked(db.session.get(Agent, a_id)))
    admin = hub["admin"]
    info = admin.get(f"/api/hosts/{DNS}/agent").get_json()
    assert info["agent"]["linked"] and info["agent"]["facts"]["hostname"] and info["agent"]["host_allow"] == []
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "ping", "params": {"echo": "hi"}})
    assert r.status_code == 201, r.get_json()
    done = job_done(admin, r.get_json()["id"])
    assert done["status"] == "succeeded" and done["result"]["pong"] and done["result"]["echo"] == "hi"
    # heartbeats bring metrics, exported for VictoriaMetrics
    wait(lambda: db.session.get(Agent, a_id).metrics.get("mem_total"))
    text = admin.get("/api/agents/metrics").get_data(as_text=True)
    assert f'labdex_agent_up{{host="{DNS}",agent="{a_id}"}} 1' in text and "labdex_agent_mem_total{" in text
    # both audit logs
    events = [e.summary for e in db.session.scalars(db.select(AuditEvent).where(AuditEvent.object_type == "agent"))]
    assert any("ping queued" in s for s in events) and any("ping on" in s and "succeeded" in s for s in events)
    lines = [json.loads(x) for x in open(os.path.join(cfg.state_dir, "audit.log"))]
    assert [x["event"] for x in lines] == ["accepted", "finished"] and lines[0]["type"] == "ping"
    agent.stop()


def test_revoke_cuts_a_live_link_at_once(hub):
    cfg, creds = enrol(hub)
    agent, th, out = start(cfg, creds)
    wait(lambda: agents.linked(db.session.get(Agent, creds["agent_id"])))
    t0 = time.time()
    assert hub["admin"].delete(f"/api/hosts/{DNS}/agent").status_code == 200
    th.join(10)
    assert not th.is_alive() and "4401" in out["refused"] and time.time() - t0 < 5
    # and it can't come back
    agent2, th2, out2 = start(cfg, creds)
    th2.join(10)
    assert "4401" in out2["refused"]


def test_write_jobs_need_the_capability_at_both_ends(hub):
    admin = hub["admin"]
    cfg, creds = enrol(hub, allow=["patch"])
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "patch.apply"})
    assert r.status_code == 409 and "switch on patch" in r.get_json()["error"]
    assert admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "rm -rf /"}).status_code == 409
    assert admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "facts", "params": {"cmd": "id"}}).status_code == 409
    assert admin.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["patch"]}).status_code == 200
    # reboot is off on the host's side
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "patch.apply", "params": {"reboot": True}})
    assert r.status_code == 409
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "patch.apply"})
    assert r.status_code == 201
    runner = FakeRunner()
    agent, th, out = start(cfg, creds, runner=runner)
    done = job_done(admin, r.get_json()["id"])
    assert done["status"] == "succeeded", done
    assert runner.calls[:2] == ["apt_update", "apt_upgrade"] and "reboot" not in runner.calls
    agent.stop()


def test_editors_cant_switch_on_writes(hub, make_user):
    enrol(hub)
    editor = make_user("eddie", role="editor")
    assert editor.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["patch"]}).status_code == 403
    assert editor.patch(f"/api/hosts/{DNS}/agent", json={"discovery_every": 30}).status_code == 200
    assert editor.patch(f"/api/hosts/{DNS}/agent", json={"discovery_every": 2}).status_code == 422


def test_agent_refuses_bad_jobs(hub, tmp_path):
    cfg, creds = enrol(hub)
    sent = []
    agent = core.Agent(cfg, creds, runner=FakeRunner(), log=lambda m: None)
    agent._send = lambda m: sent.append(m) or True
    ran = []
    agent.pool.submit = lambda fn, env: ran.append(env)
    a = db.session.get(Agent, creds["agent_id"])
    good = agents.signed(agents.queue(a, "ping"))
    agent.take(good)
    assert len(ran) == 1
    agent.take(good)  # the same job again: a replay
    assert len(ran) == 1
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    now = time.time()
    env = {"v": 1, "id": "x1", "agent": a.id, "host": DNS, "type": "ping", "params": {}, "issued": now, "expires": now + 60}
    agent.take(protocol.sign(Ed25519PrivateKey.generate(), env))          # someone else's key
    agent.take(protocol.sign(agents.signing_key(), env | {"id": "x2", "agent": "deadbeef"}))   # another agent's
    agent.take(protocol.sign(agents.signing_key(), env | {"id": "x3", "expires": now - 3600}))  # expired
    agent.take(protocol.sign(agents.signing_key(), env | {"id": "x4", "type": "shell"}))        # not a job type
    agent.take(protocol.sign(agents.signing_key(), env | {"id": "x5", "type": "patch.apply"}))  # not allowed here
    tampered = protocol.sign(agents.signing_key(), env | {"id": "x6"})
    tampered["job"] = tampered["job"].replace('"ping"', '"facts"')
    agent.take(tampered)
    assert len(ran) == 1
    refused = {m["id"]: m["error"] for m in sent if m.get("t") == "result"}
    assert "bad signature" in refused["x1"] and "another agent" in refused["x2"] and "expired" in refused["x3"]
    assert "unknown job type" in refused["x4"] and "capability" in refused["x5"] and "bad signature" in refused["x6"]
    audit = [json.loads(x) for x in open(os.path.join(cfg.state_dir, "audit.log"))]
    assert sum(1 for x in audit if x["event"] == "refused") == 7


def test_results_wait_in_the_buffer_while_the_hub_is_away(hub):
    cfg, creds = enrol(hub)
    agent = core.Agent(cfg, creds, runner=FakeRunner(), log=lambda m: None)
    a = db.session.get(Agent, creds["agent_id"])
    j = agents.queue(a, "facts")
    j.status = "sent"
    db.session.commit()
    msg = agents.signed(j)
    agent.take(msg)  # no link: the ack is lost, the result goes to the buffer
    wait(lambda: len(agent.spool) == 1)
    assert db.session.get(AgentJob, j.id).status == "sent"
    agent.pool = core.ThreadPoolExecutor(max_workers=1)
    th = threading.Thread(target=lambda: agent.run(), daemon=True)
    th.start()
    wait(lambda: db.session.get(AgentJob, j.id).status == "succeeded")
    assert len(agent.spool) == 0 and db.session.get(AgentJob, j.id).result["hostname"]
    agent.stop()


# ----------------------------------------------------------------------------- discovery
def test_redaction():
    argv = ["app", "--password", "s3cret", "--token=abc", "-p", "8080", "DB_PASSWORD=x", "api_key:y",
            "postgres://bob:pw@db.example.com/x", "--verbose", "--keyfile", "/etc/k.pem"]
    out = discovery.redact(argv)
    assert out == ["app", "--password", "***", "--token=***", "-p", "8080", "DB_PASSWORD=***", "api_key:***",
                   "postgres://bob:***@db.example.com/x", "--verbose", "--keyfile", "/etc/k.pem"]


def test_parsers():
    socks = discovery.parse_ss(SS)
    assert [(s["proto"], s["address"], s["port"]) for s in socks] == [
        ("tcp", "0.0.0.0", 22), ("tcp", "::", 22), ("tcp", "127.0.0.53", 53), ("udp", "0.0.0.0", 53),
        ("tcp", "0.0.0.0", 5380), ("tcp", "127.0.0.1", 6379)]
    assert socks[3]["pids"] == [{"name": "dnsd", "pid": 900}]
    assert discovery.unit_of_cgroup("0::/system.slice/docker-abc.scope\n") == "docker-abc.scope"
    assert discovery.unit_of_cgroup("0::/user.slice/user-1000.slice/session-3.scope\n") == "session-3.scope"
    assert discovery.unit_of_cgroup("0::/init.scope/x\n") == "init.scope"
    assert discovery.unit_of_cgroup("") == ""
    units = discovery.parse_show(SHOW)
    assert units[0] == {"unit": "ssh.service", "description": "OpenBSD Secure Shell server", "pid": 812, "user": "root",
                        "path": "/lib/systemd/system/ssh.service", "since": "Mon 2026-11-16 08:00:00 UTC"}
    c = discovery.parse_docker(DOCKER)[0]
    assert c["name"] == "technitium" and c["compose_project"] == "dns" and c["id"] == "0123456789ab"
    assert {(p["address"], p["port"]) for p in c["ports"]} == {("0.0.0.0", 5380), ("::", 5380)}
    assert len(discovery.parse_docker_ports("0.0.0.0:8000-8002->8000-8002/udp")) == 3
    pod = discovery.parse_podman(json.dumps([{"Id": "abcdef1234567890", "Names": ["web"], "Image": "nginx", "State": "running",
                                               "Ports": [{"host_ip": "", "host_port": 8080, "container_port": 80, "protocol": "tcp", "range": 1}]}]))
    assert pod[0]["ports"] == [{"address": "0.0.0.0", "port": 8080, "container_port": 80, "proto": "tcp"}]


def test_discovery_collect(tmp_path):
    runner = FakeRunner()
    out = discovery.collect(runner, proc=fake_proc(tmp_path))
    procs = {p["pid"]: p for p in out["processes"]}
    assert procs[900]["uid"] == 107 and procs[900]["unit"] == "dnsd.service"
    assert procs[900]["cmdline"] == "/usr/bin/dnsd --admin-password *** --token=*** DB_PASS=*** --upstream https://user:***@dns.example.com/q"
    assert procs[1500]["parent"] == {"pid": 700, "name": "dockerd"} and 700 in procs  # the parent comes along
    assert procs[812]["parent"] == {"pid": 1, "name": "systemd"} and procs[812]["started"] > 0
    by = {(s["proto"], s["address"], s["port"]): s for s in out["listeners"]}
    assert by[("tcp", "0.0.0.0", 22)]["units"] == ["ssh.service"]
    assert by[("tcp", "0.0.0.0", 5380)]["container"] == {"engine": "docker", "name": "technitium", "image": "technitium/dns-server:13"}
    assert [u["unit"] for u in out["units"]] == ["ssh.service", "dnsd.service"]
    assert out["containers"][0]["name"] == "technitium" and out["notes"] == []
    # read-only: nothing but the read-only privileged lines and systemctl queries
    assert set(runner.calls) <= {"ss", "docker_ps", "systemctl list-units", "systemctl show"}


def test_discovery_without_sudo_still_lists_ports(tmp_path):
    class NoSudo(FakeRunner):
        def privileged(self, name, timeout=60):
            return 1, "", "sudo: a password is required"

        def run(self, argv, timeout=60):
            if argv[0] == "ss":
                return 0, SS.replace('users:(("sshd",pid=812,fd=3))', ""), ""
            return super().run(argv, timeout)
    out = discovery.collect(NoSudo(), proc=fake_proc(tmp_path))
    assert out["listeners"] and any("sudo rule for ss" in n for n in out["notes"])


def test_discovery_lands_in_discovery_and_on_the_host_page(hub, tmp_path):
    cfg, creds = enrol(hub)
    import labdex.agent.discovery as d
    orig = d.collect
    d.collect = lambda runner, proc=None: orig(runner, proc=fake_proc(tmp_path))
    try:
        agent, th, out = start(cfg, creds)
        r = hub["admin"].post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "discovery.services"})
        done = job_done(hub["admin"], r.get_json()["id"])
    finally:
        d.collect = orig
    assert done["status"] == "succeeded", done
    rows = {(s.ip, s.port, s.proto): s for s in db.session.scalars(db.select(DiscoveredService))}
    assert rows[("10.10.100.53", 22, "tcp")].service == "ssh.service" and rows[("10.10.100.53", 22, "tcp")].source == "agent"
    assert rows[("10.10.100.53", 5380, "tcp")].product == "technitium/dns-server:13"
    assert ("fd00:10:10:100::53", 22, "tcp") in rows  # [::]:22 listens on the IPv6 address too
    assert not any(p in (6379,) for (_, p, _) in rows)  # loopback-only stays on the host page
    page = hub["admin"].get(f"/api/hosts/{DNS}/agent").get_json()
    assert any(p["cmdline"].startswith("/usr/bin/dnsd") for p in page["discovery"]["processes"])
    # the next run doesn't see port 22: it closes
    a = db.session.get(Agent, creds["agent_id"])
    agents.record_discovery(a, {"listeners": [], "processes": []})
    assert db.session.scalar(db.select(DiscoveredService).where(DiscoveredService.port == 22, DiscoveredService.ip == "10.10.100.53")).state == "closed"
    agent.stop()


def test_schedule_queues_discovery_and_patch_reports(hub):
    cfg, creds = enrol(hub)
    a = db.session.get(Agent, creds["agent_id"])
    assert agents.tick()["queued"] == 0  # not linked: nothing to queue
    a.connected, a.last_seen = "w", agents.utcnow()
    assert agents.tick()["queued"] == 2
    assert agents.tick()["queued"] == 0  # still waiting
    types = {j.type for j in db.session.scalars(db.select(AgentJob))}
    assert types == {"discovery.services", "patch.report"}
    a.discovery_every = 0
    for j in db.session.scalars(db.select(AgentJob)):
        j.status, j.created_at = "succeeded", j.created_at - agents.timedelta(days=1)
    assert agents.tick()["queued"] == 1  # discovery is off now


def test_sudoers_rules():
    rules = jobs.sudoers("labdex-agent", set())
    assert "Defaults:labdex-agent !requiretty" in rules
    assert "ss -Htulpn" in rules or "ss is not installed" in rules
    assert "apt-get" not in rules and "shutdown" not in rules
    with_patch = jobs.sudoers("labdex-agent", {"patch"})
    if "apt-get" in with_patch:
        assert "Dpkg\\:\\:Options\\:\\:\\=--force-confold" in with_patch


# ----------------------------------------------------------------------------- patches and ansible
def test_patch_report_lands_in_patch_state(hub, monkeypatch):
    from labdex.agent import patch_report
    monkeypatch.setattr(patch_report, "collect", lambda refresh=False: {"os": "Ubuntu 24.04", "kernel": "6.8", "pending": 1, "reboot": True,
                                                                        "packages": [{"name": "openssl", "from": "1", "to": "2", "security": True}]})
    cfg, creds = enrol(hub)
    agent, th, out = start(cfg, creds)
    r = hub["admin"].post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "patch.report"})
    assert job_done(hub["admin"], r.get_json()["id"])["status"] == "succeeded"
    row = db.session.scalar(db.select(HostPatch).where(HostPatch.host == DNS, HostPatch.source == "agent"))
    assert row.pending == 1 and row.security == 1 and row.reboot
    agent.stop()


def _tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/shadow"
                t.addfile(info)
                continue
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_bundles_unpack_safely(tmp_path):
    agent_ansible.unpack(_tgz({"ansible/site.yml": b"- hosts: all\n"}), str(tmp_path / "ok"))
    assert (tmp_path / "ok" / "ansible" / "site.yml").exists()
    for bad in ({"../evil.yml": b"x"}, {"/etc/cron.d/x": b"x"}, {"ansible/link": None}):
        with pytest.raises(protocol.BadJob):
            agent_ansible.unpack(_tgz(bad), str(tmp_path / "bad"))
    with pytest.raises(protocol.BadJob):
        agent_ansible.check_params({"bundle": "0" * 64, "playbook": "../x.yml"})
    with pytest.raises(protocol.BadJob):
        agent_ansible.check_params({"bundle": "0" * 64, "playbook": "site.yml", "cmd": "id"})


def test_ansible_needs_root_and_a_matching_bundle(tmp_path):
    data = _tgz({"ansible/site.yml": b"- hosts: all\n"})
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    params = {"bundle": sha, "playbook": "ansible/site.yml", "vars": {"x": 1}}

    class Root(FakeRunner):
        def run(self, argv, timeout=60):
            self.calls.append(argv)
            return 0, "PLAY RECAP\nlocalhost : ok=1 changed=0\n", ""
    ctx = jobs.Context(FakeRunner(), {"ansible"}, str(tmp_path), fetch_bundle=lambda s: data)
    with pytest.raises(protocol.BadJob, match="root"):
        agent_ansible.run(params, ctx)
    runner = Root(root=True, programs={"ansible-playbook"})
    ctx = jobs.Context(runner, {"ansible"}, str(tmp_path), fetch_bundle=lambda s: data + b"x")
    with pytest.raises(protocol.BadJob, match="signed hash"):
        agent_ansible.run(params, ctx)
    ctx = jobs.Context(runner, {"ansible"}, str(tmp_path), fetch_bundle=lambda s: data)
    out = agent_ansible.run(params | {"check": True}, ctx)
    argv = runner.calls[-1]
    assert out["ok"] and "PLAY RECAP" in out["recap"] and argv[0] == "systemd-run" and "--check" in argv
    assert argv[argv.index("ansible-playbook"):][:5] == ["ansible-playbook", "-i", "localhost,", "-c", "local"]
    assert not (tmp_path / "bundles" / sha).exists()  # cleaned up


def test_ansible_job_signs_a_bundle_from_git(hub):
    import subprocess
    from labdex import gitsource, sites
    repo = gitsource.repo_dir(sites.default())
    (repo / "ansible").mkdir(parents=True)
    (repo / "ansible" / "site.yml").write_text("- hosts: all\n  tasks: []\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=ci@example.com", "-c", "user.name=ci", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    admin = hub["admin"]
    cfg, creds = enrol(hub, allow=["ansible"])
    assert admin.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["ansible"]}).status_code == 200
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "ansible.run", "params": {"playbook": "ansible/site.yml", "check": True}})
    assert r.status_code == 201, r.get_json()
    p = r.get_json()["params"]
    assert len(p["bundle"]) == 64 and len(p["commit"]) == 40 and p["check"] is True
    a = db.session.get(Agent, creds["agent_id"])
    assert agents.bundle(a, p["bundle"]) is None  # not sent yet: not fetchable
    db.session.get(AgentJob, r.get_json()["id"]).status = "sent"
    data = agents.bundle(a, p["bundle"])
    import hashlib
    assert hashlib.sha256(data).hexdigest() == p["bundle"]
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert "ansible/site.yml" in names


def test_ansible_takes_no_site_and_reports_a_missing_folder(hub):
    import subprocess
    from labdex import gitsource, sites
    repo = gitsource.repo_dir(sites.default())
    (repo / "ansible").mkdir(parents=True)
    (repo / "ansible" / "site.yml").write_text("- hosts: all\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=ci@example.com", "-c", "user.name=ci", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    admin = hub["admin"]
    enrol(hub, allow=["ansible"])
    admin.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["ansible"]})
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "ansible.run", "params": {"site": "../../etc", "playbook": "ansible/site.yml"}})
    assert r.status_code == 409 and "unknown parameters: site" in r.get_json()["error"]
    r = admin.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "ansible.run", "params": {"playbook": "nope/site.yml"}})
    assert r.status_code == 409 and "couldn't pack nope/" in r.get_json()["error"]
