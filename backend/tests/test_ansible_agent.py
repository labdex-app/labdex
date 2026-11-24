"""Ansible through the agent: the SSH CA in the vault, per-run certificates, the pipe broker, the
ansible.session job, and a real run: ansible-playbook in labdex, ssh through the Go agent's pipe
to a real sshd. Design: docs/develop/design/ansible-through-the-agent.md."""
import getpass
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from labdex import agent_pipes, agents, ansible_agent
from labdex.agent import protocol
from labdex.models import Agent, AgentJob, db

from . import test_agents, test_go_agent
from .test_agents import DNS, wait

hub = test_agents.hub
tls_hub = test_agents.tls_hub
go_agent = test_go_agent.go_agent
PASS = "correct horse battery staple"


def unseal(admin):
    assert admin.post("/api/vault/init", json={"passphrase": PASS}).status_code in (200, 201, 204)


# ----------------------------------------------------------------------------- certificates
def test_certificates_are_per_host_short_and_only_from_localhost():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import SSHCertificateType, load_ssh_private_key, load_ssh_public_identity
    ca = Ed25519PrivateKey.generate()
    priv, line = ansible_agent.certificate(ca, DNS, "run-1/admin/" + DNS, minutes=10)
    cert = load_ssh_public_identity(line)
    assert cert.type == SSHCertificateType.USER
    assert cert.valid_principals == [f"labdex-ansible@{DNS}".encode()]
    assert cert.critical_options == {b"source-address": b"127.0.0.1/32,::1/128"}
    assert cert.extensions == {}  # no PTY, no forwarding
    assert 10 * 60 < cert.valid_before - time.time() <= 11 * 60 and cert.valid_after < time.time()
    cert.verify_cert_signature()
    assert cert.signature_key().public_bytes_raw() == ca.public_key().public_bytes_raw()
    assert load_ssh_private_key(priv, None).public_key().public_bytes_raw() == cert.public_key().public_bytes_raw()


def test_the_ca_lives_in_the_vault(admin, loaded):
    with pytest.raises(ansible_agent.AnsibleAgentError, match="vault is sealed"):
        ansible_agent.ca_key()
    db.session.rollback()
    unseal(admin)
    first = ansible_agent.ca_public()
    assert first.startswith("ssh-ed25519 ") and first.endswith(" labdex-ansible-ca")
    assert ansible_agent.ca_public() == first  # made once
    assert admin.get("/api/vault/entries").status_code in (200, 404)  # listed like any entry (when the API lists)


def test_known_hosts_come_from_the_agents_facts():
    a = Agent(facts={"ssh_host_keys": ["ssh-ed25519 AAAAC3", "ssh-dss AAAAB3", "garbage", 7]})
    assert ansible_agent.known_hosts_lines(DNS, a) == [f"{DNS} ssh-ed25519 AAAAC3"]


# ----------------------------------------------------------------------------- the session job
def test_the_session_job_goes_only_to_agents_that_run_it(hub):
    cfg, creds = test_agents.enrol(hub, allow=["ansible"])
    a = db.session.get(Agent, creds["agent_id"])
    a.capabilities, a.host_allow = ["ansible"], ["ansible"]
    db.session.commit()
    params = {"session": "run-0123456789", "minutes": 5}
    with pytest.raises(agents.AgentError, match="doesn't run ansible.session"):
        agents.queue(a, "ansible.session", params)
    a.jobs = ["ansible.session", "ping"]
    for bad in ({"session": "short", "minutes": 5}, {"session": "run-0123456789", "minutes": 61},
                {"session": "run-0123456789", "minutes": True}, params | {"port": 2222}):
        with pytest.raises(agents.AgentError, match="takes session"):
            agents.queue(a, "ansible.session", bad)
    a.capabilities = []
    with pytest.raises(agents.AgentError, match="switch on ansible"):
        agents.queue(a, "ansible.session", params)
    a.capabilities = ["ansible"]
    j = agents.queue(a, "ansible.session", params)
    assert j.type == "ansible.session" and j.params == params
    db.session.rollback()
    # the Python agent stays as it is: the shared list doesn't have it
    assert "ansible.session" not in protocol.JOB_TYPES and "ansible.session" in agents.JOB_TYPES


def test_become_is_an_administrators_switch(hub, make_user):
    test_agents.enrol(hub)
    editor = make_user("ed", roles=["editor"]) if "roles" in make_user.__code__.co_varnames else None
    if editor is not None:
        assert editor.patch(f"/api/hosts/{DNS}/agent", json={"ansible_become": True}).status_code == 403
    r = hub["admin"].patch(f"/api/hosts/{DNS}/agent", json={"ansible_become": True})
    assert r.status_code == 200 and r.get_json()["ansible_become"] is True


def test_hosts_fetch_the_ca_public_key_without_signing_in(admin, loaded):
    from labdex import keyvault
    keyvault.seal()  # an earlier test may have left it open
    anon = loaded.test_client()
    r = anon.get("/api/agents/ansible-ca.pub")
    assert r.status_code == 503 and "sealed" in r.get_json()["error"]
    db.session.rollback()
    unseal(admin)
    r = anon.get("/api/agents/ansible-ca.pub")
    assert r.status_code == 200 and r.text == ansible_agent.ca_public() + "\n"
    assert "PRIVATE" not in r.text


def test_a_new_token_keeps_the_hosts_ansible_set_up(hub, loaded):
    bin_dir = hub["tmp"] / "bin"
    bin_dir.mkdir()
    (bin_dir / "labdex-agent-linux-amd64").write_bytes(b"not really an agent")
    loaded.config["LABDEX_AGENT_BIN_DIR"] = str(bin_dir)
    cfg, creds = test_agents.enrol(hub)
    a = db.session.get(Agent, creds["agent_id"])
    a.jobs, a.host_allow, a.ansible_become = ["ping", "ansible.session"], ["ansible"], True
    db.session.commit()
    out = hub["admin"].post(f"/api/hosts/{DNS}/agent/token", json={}).get_json()
    assert ' --allow "ansible"' in out["python"]["command"] and "--ansible " not in out["python"]["command"] + " "
    assert '--allow "ansible" --ansible --ansible-become' in out["go"]["command"]


# ----------------------------------------------------------------------------- the broker
class FakeAgent:
    """An agent's end of the link: pipes to a TCP echo server, like the Go agent to its sshd."""

    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.conns = {}
        self.sent = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        threading.Thread(target=self._echo, daemon=True).start()

    def _echo(self):
        while True:
            try:
                c, _ = self.server.accept()
            except OSError:
                return

            def loop(c=c):
                try:
                    while data := c.recv(4096):
                        c.sendall(data)
                except OSError:
                    pass
                c.close()
            threading.Thread(target=loop, daemon=True).start()

    def send(self, msg):  # what the hub sends to the agent
        self.sent.append(msg)
        t = msg["t"]
        if t == "pipe.open":
            c = socket.create_connection(self.server.getsockname())
            self.conns[msg["id"]] = c

            def back(pid=msg["id"], c=c):
                try:
                    while data := c.recv(4096):
                        agent_pipes.from_agent(self.agent_id, {"t": "pipe.data", "id": pid, "data": protocol.b64(data)})
                except OSError:  # closed from the hub's side
                    return
                agent_pipes.from_agent(self.agent_id, {"t": "pipe.close", "id": pid})
            threading.Thread(target=back, daemon=True).start()
            agent_pipes.from_agent(self.agent_id, {"t": "pipe.opened", "id": msg["id"]})
        elif t == "pipe.data":
            self.conns[msg["id"]].sendall(protocol.unb64(msg["data"]))
        elif t == "pipe.close":
            c = self.conns.pop(msg["id"], None)
            if c:
                c.close()


def _client(run, host, data: bytes):
    client = Path(ansible_agent.__file__).with_name("agent_pipe_client.py")
    return subprocess.run([sys.executable, "-I", str(client), str(run.path), run.token, host],
                          input=data, capture_output=True, timeout=20)


def test_the_broker_moves_bytes_both_ways(loaded, tmp_path, monkeypatch):
    fake = FakeAgent("ag1")
    send = fake.send
    agent_pipes.linked("ag1", send, threading.Event())
    monkeypatch.setattr(agent_pipes.Run, "_queue_session", lambda self, agent_id: None)
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    run = agent_pipes.Run(loaded, run_dir, {DNS: "ag1"})
    try:
        assert os.stat(run.path).st_mode & 0o077 == 0
        big = os.urandom(200_000)  # several pipe.data chunks each way
        client = Path(ansible_agent.__file__).with_name("agent_pipe_client.py")
        # like ssh: it keeps its end open until it has what it waits for (pipes have no half-close)
        p = subprocess.Popen([sys.executable, "-I", str(client), str(run.path), run.token, DNS],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdin.write(big)
        p.stdin.flush()
        got = b""
        while len(got) < len(big):
            chunk = p.stdout.read1(65536)
            if not chunk:
                break
            got += chunk
        p.stdin.close()
        assert p.wait(10) == 0 and got == big, p.stderr.read()
        assert [m["t"] for m in fake.sent][0] == "pipe.open" and fake.sent[0]["session"] == run.session
        # a wrong token or a host that isn't in the run gets no pipe
        bad = subprocess.run([sys.executable, "-I", str(Path(ansible_agent.__file__).with_name("agent_pipe_client.py")),
                              str(run.path), "nope", DNS], capture_output=True, timeout=10)
        assert bad.returncode == 1 and b"not this run's token" in bad.stderr
        other = _client(run, "srv-vm-other-01", b"x")
        assert other.returncode == 1 and b"doesn't go through its agent" in other.stderr
    finally:
        run.close()
        agent_pipes.unlinked("ag1", send)
    assert fake.sent[-1] == {"t": "session.end", "session": run.session}
    assert not run.path.exists()


def test_a_dropped_link_closes_its_pipes(loaded, tmp_path, monkeypatch):
    fake = FakeAgent("ag2")
    send = fake.send  # one object, like the link's own send
    agent_pipes.linked("ag2", send, threading.Event())
    monkeypatch.setattr(agent_pipes.Run, "_queue_session", lambda self, agent_id: None)
    run = agent_pipes.Run(loaded, tmp_path, {DNS: "ag2"})
    client = Path(ansible_agent.__file__).with_name("agent_pipe_client.py")
    p = subprocess.Popen([sys.executable, "-I", str(client), str(run.path), run.token, DNS],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait(lambda: any(m["t"] == "pipe.open" for m in fake.sent))
        agent_pipes.unlinked("ag2", send)
        assert p.wait(10) == 0  # ssh would see the connection end
        assert not agent_pipes.is_linked("ag2")
    finally:
        p.kill()
        run.close()


# ----------------------------------------------------------------------------- a real run
def _need(*programs):
    for prog in programs:
        if not shutil.which(prog) and not os.path.exists(prog):
            pytest.skip(f"{prog} isn't installed")


@pytest.fixture
def sshd(tmp_path):
    """A real sshd on a free localhost port that trusts only the CA passed to start()."""
    _need("/usr/sbin/sshd", "ssh", "ssh-keygen")
    d = tmp_path / "sshd"
    d.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(d / "host_key")], check=True)
    os.makedirs("/run/sshd", exist_ok=True) if os.geteuid() == 0 else None
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    procs = []

    def start(ca_line: str, principal: str):
        (d / "ca.pub").write_text(ca_line + "\n")
        (d / "principals").write_text(f'from="127.0.0.1,::1" {principal}\n')
        (d / "sshd_config").write_text(f"""Port {port}
ListenAddress 127.0.0.1
HostKey {d / 'host_key'}
PidFile {d / 'sshd.pid'}
TrustedUserCAKeys {d / 'ca.pub'}
AuthorizedPrincipalsFile {d / 'principals'}
AuthorizedKeysFile none
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes no
PermitTTY no
AllowTcpForwarding no
X11Forwarding no
""")
        p = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(d / "sshd_config")],
                             stdout=open(d / "sshd.log", "w"), stderr=subprocess.STDOUT)
        procs.append(p)
        end = time.time() + 10
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        return {"port": port, "host_key": (d / "host_key.pub").read_text().split()[:2], "log": d / "sshd.log"}

    yield start
    for p in procs:
        p.kill()


def test_ansible_runs_through_the_go_agent(go_agent, tls_hub, sshd, tmp_path, monkeypatch):
    _need("ansible-playbook")
    admin = tls_hub["admin"]
    unseal(admin)
    user = getpass.getuser()
    monkeypatch.setattr(ansible_agent, "USER", user)  # sshd here logs in as whoever runs the tests
    server = sshd(ansible_agent.ca_public(), f"{user}@{DNS}")
    conf, state, creds = test_go_agent.go_enrol(go_agent, tls_hub, "goa")
    Path(conf).write_text(Path(conf).read_text().replace("allow =", "allow = ansible") + f"ssh_port = {server['port']}\n")
    p = test_go_agent.go_run(go_agent, conf, tls_hub["tmp"] / "goa.log")
    try:
        wait(lambda: agents.linked(db.session.get(Agent, creds["agent_id"])), timeout=20)
        assert admin.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["ansible"]}).status_code == 200
        a = db.session.get(Agent, creds["agent_id"])
        assert "ansible.session" in a.jobs and a.host_allow == ["ansible"]
        # the sshd here has its own host key: pin that one instead of this machine's
        a.facts = (a.facts or {}) | {"ssh_host_keys": [" ".join(server["host_key"])]}
        db.session.commit()
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        inv = {"all": {"hosts": {DNS: {}, "srv-vm-plain-01": {}}}}
        run = ansible_agent.attach(inv, run_dir, by="test")
        assert run is not None and set(run.hosts) == {DNS}
        hv = inv["all"]["hosts"][DNS]
        assert hv["ansible_user"] == user and hv["ansible_become"] is False and "ProxyCommand" in hv["ansible_ssh_common_args"]
        assert inv["all"]["hosts"]["srv-vm-plain-01"] == {}  # no agent: as before
        import yaml
        (run_dir / "inventory.yaml").write_text(yaml.safe_dump(inv))
        (run_dir / "site.yml").write_text(yaml.safe_dump([
            {"hosts": DNS, "gather_facts": False, "tasks": [
                {"name": "who", "ansible.builtin.command": "id -un", "register": "who", "changed_when": False},
                {"name": "say", "ansible.builtin.debug": {"msg": "logged in as {{ who.stdout }}"}},
                {"name": "become stays off", "ansible.builtin.command": "id -un", "become": True,
                 "register": "who2", "changed_when": False},
                {"name": "say2", "ansible.builtin.debug": {"msg": "became {{ who2.stdout }}"}},
            ]}]))
        env = os.environ | {"HOME": str(run_dir), "ANSIBLE_NOCOLOR": "1", "ANSIBLE_CONFIG": str(run_dir / "none.cfg"),
                            "ANSIBLE_LOCAL_TEMP": str(run_dir / "atmp")}
        try:
            r = subprocess.run(["ansible-playbook", "-i", str(run_dir / "inventory.yaml"), str(run_dir / "site.yml")],
                               capture_output=True, text=True, env=env, timeout=180)
        finally:
            run.close()
        out = r.stdout + r.stderr
        assert r.returncode == 0, out + "\n--- sshd\n" + server["log"].read_text()
        assert f"logged in as {user}" in out and f"became {user}" in out
        # the agent opened one session, and its audit log shows the pipes but never their bytes
        sessions = list(db.session.scalars(db.select(AgentJob).where(AgentJob.type == "ansible.session")))
        assert len(sessions) == 1 and sessions[0].status == "succeeded" and sessions[0].created_by == "test"
        events = [json.loads(line) for line in (state / "audit.log").read_text().splitlines()]
        kinds = [e["event"] for e in events]
        assert "session.open" in kinds and "pipe.open" in kinds and "pipe.close" in kinds
        wait(lambda: "session.end" in (state / "audit.log").read_text())
        assert "logged in as" not in (state / "audit.log").read_text()
        # sshd names the run in its log: the certificate's key id
        assert f"{run.session}/test/{DNS}" in server["log"].read_text()
    finally:
        p.terminate()
        p.wait(10)


# ----------------------------------------------------------------------------- install.sh --ansible
def _ubuntu() -> bool:
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return False
    return "ID=ubuntu" in text or "ID_LIKE=ubuntu" in text or 'ID_LIKE="ubuntu' in text


@pytest.mark.skipif(os.environ.get("LABDEX_INSTALL_TEST") != "1" or os.geteuid() != 0 or not _ubuntu(),
                    reason="changes this machine (users, sshd, sudo): only in a throwaway Ubuntu container "
                           "as root, with LABDEX_INSTALL_TEST=1")
def test_install_sh_sets_up_a_host_for_ansible(go_agent, tls_hub, loaded, tmp_path, monkeypatch):
    """The whole path on a real Ubuntu: install.sh --ansible --ansible-become, the host's own sshd,
    the Go agent as its own user, a playbook that logs in as labdex-ansible and becomes root, and
    --uninstall --purge taking it all away again. systemd is stood in for: the test starts the agent
    and sshd itself."""
    _need("/usr/sbin/sshd", "ansible-playbook", "runuser")
    admin = tls_hub["admin"]
    unseal(admin)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(go_agent, bin_dir / "labdex-agent-linux-amd64")
    loaded.config["LABDEX_AGENT_BIN_DIR"] = str(bin_dir)
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/systemctl.log\n')
    (stub / "systemctl").chmod(0o755)
    env = os.environ | {"PATH": f"{stub}:{os.environ['PATH']}"}
    script = Path(test_agents.core.__file__).parent / "dist" / "install.sh"
    install = shutil.copy(script, tmp_path / "install.sh")
    # the agent's own user reads the hub's CA: somewhere it can reach, unlike pytest's root-only folders
    ca_file = Path("/etc/labdex-test-hub-ca.pem")
    shutil.copy(tls_hub["ca_file"], ca_file)
    ca_file.chmod(0o644)
    token = admin.post(f"/api/hosts/{DNS}/agent/token", json={}).get_json()["token"]
    r = subprocess.run(["sh", install, "--url", tls_hub["url"], "--token", token, "--ca-file", str(ca_file),
                        "--ansible", "--ansible-become", "--packages", "acl"],
                       capture_output=True, text=True, env=env, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"labdex-ansible@{DNS}, and may become root" in r.stdout
    conf = Path("/etc/labdex-agent/agent.conf").read_text()
    assert "allow = ansible" in conf and "ssh_port = 22" in conf
    assert Path("/etc/ssh/labdex-ansible-principals").read_text() == f'from="127.0.0.1,::1" labdex-ansible@{DNS}\n'
    assert Path("/etc/ssh/labdex-ansible-ca.pub").read_text().strip() == ansible_agent.ca_public()
    assert "NOPASSWD: ALL" in Path("/etc/sudoers.d/labdex-ansible").read_text()
    assert shutil.which("setfacl")  # --packages acl
    # sshd's view: the drop-in reaches labdex-ansible and nobody else
    t = subprocess.run(["/usr/sbin/sshd", "-T", "-C", "user=labdex-ansible,host=x,addr=127.0.0.1"],
                       capture_output=True, text=True, check=True).stdout
    assert "trustedusercakeys /etc/ssh/labdex-ansible-ca.pub" in t and "permittty no" in t
    t = subprocess.run(["/usr/sbin/sshd", "-T", "-C", "user=root,host=x,addr=127.0.0.1"],
                       capture_output=True, text=True, check=True).stdout
    assert "trustedusercakeys none" in t
    sshd = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e"], stdout=open(tmp_path / "sshd.log", "w"), stderr=subprocess.STDOUT)
    agent = subprocess.Popen(["runuser", "-u", "labdex-agent", "--", "/opt/labdex-agent/labdex-agent",
                              "--config", "/etc/labdex-agent/agent.conf", "run"],
                             stdout=open(tmp_path / "agent.log", "w"), stderr=subprocess.STDOUT)
    try:
        wait(lambda: (x := agents.current(DNS)) is not None and agents.linked(x), timeout=30)
        a = agents.current(DNS)
        assert a.host_allow == ["ansible"] and "ansible.session" in a.jobs
        assert admin.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["ansible"], "ansible_become": True}).status_code == 200
        db.session.expire_all()
        wait(lambda: (db.session.get(Agent, a.id).facts or {}).get("ssh_host_keys"), timeout=30)
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        inv = {"all": {"hosts": {DNS: {}}}}
        run = ansible_agent.attach(inv, run_dir, by="test")
        assert run is not None
        import yaml
        (run_dir / "inventory.yaml").write_text(yaml.safe_dump(inv))
        (run_dir / "site.yml").write_text(yaml.safe_dump([
            {"hosts": DNS, "gather_facts": True, "tasks": [
                {"name": "who", "ansible.builtin.command": "id -un", "register": "who", "changed_when": False},
                {"name": "root", "ansible.builtin.command": "id -un", "become": True, "register": "root", "changed_when": False},
                {"name": "say", "ansible.builtin.debug": {"msg": "as {{ who.stdout }} became {{ root.stdout }}"}},
            ]}]))
        penv = os.environ | {"HOME": str(run_dir), "ANSIBLE_NOCOLOR": "1", "ANSIBLE_CONFIG": str(run_dir / "none.cfg"),
                             "ANSIBLE_LOCAL_TEMP": str(run_dir / "atmp")}
        try:
            r = subprocess.run(["ansible-playbook", "-i", str(run_dir / "inventory.yaml"), str(run_dir / "site.yml")],
                               capture_output=True, text=True, env=penv, timeout=300)
        finally:
            run.close()
        out = r.stdout + r.stderr
        assert r.returncode == 0, out + "\n--- sshd\n" + (tmp_path / "sshd.log").read_text()
        assert "as labdex-ansible became root" in out
    finally:
        agent.terminate()
        agent.wait(10)
        sshd.terminate()
        sshd.wait(10)
        r = subprocess.run(["sh", install, "--uninstall", "--purge"], capture_output=True, text=True, env=env, timeout=120)
        ca_file.unlink()
    assert r.returncode == 0, r.stdout + r.stderr
    for gone in ("/etc/ssh/sshd_config.d/10-labdex-ansible.conf", "/etc/ssh/labdex-ansible-ca.pub",
                 "/etc/ssh/labdex-ansible-principals", "/etc/sudoers.d/labdex-ansible", "/var/lib/labdex-ansible"):
        assert not os.path.exists(gone), gone
    assert subprocess.run(["id", "labdex-ansible"], capture_output=True).returncode != 0
