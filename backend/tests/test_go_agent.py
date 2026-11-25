"""The Go agent (agent/) against this labdex: enrolment with a client certificate, the link over
mTLS, jobs, renewal and revocation. It runs the real binary against a real local hub over TLS;
jobs that read the host only read this machine (ping, facts). Skipped when Go isn't installed.
The protocol is docs/develop/agent-protocol.md."""
import json
import os
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest

from labdex import agents
from labdex.models import Agent, db, utcnow

from . import test_agents
from .test_agents import DNS, job_done, wait

# the live hubs of test_agents, plain and over TLS with client certificates required
hub = test_agents.hub
tls_hub = test_agents.tls_hub

AGENT_SRC = Path(__file__).resolve().parents[2] / "agent"


@pytest.fixture(scope="module")
def go_agent(tmp_path_factory):
    binary = os.environ.get("LABDEX_GO_AGENT")
    if binary:
        return binary
    if not shutil.which("go"):
        pytest.skip("Go isn't installed")
    out = tmp_path_factory.mktemp("go") / "labdex-agent"
    subprocess.run(["go", "build", "-o", str(out), "./cmd/labdex-agent"], cwd=AGENT_SRC, check=True,
                   env=os.environ | {"CGO_ENABLED": "0"}, timeout=600)
    return str(out)


def config(hub, name="go1"):
    state = hub["tmp"] / name
    conf = hub["tmp"] / f"{name}.conf"
    conf.write_text(f"[agent]\nurl = {hub['url']}\nallow =\nca_file = {hub['ca_file']}\nstate_dir = {state}\n")
    return str(conf), state


def go_enrol(go_agent, hub, name="go1"):
    conf, state = config(hub, name)
    # in a container the tests may run as root: the host has to allow a root agent then
    token = hub["admin"].post(f"/api/hosts/{DNS}/agent/token", json={"allow_root": os.geteuid() == 0}).get_json()["token"]
    r = subprocess.run([go_agent, "--config", conf, "enrol", "--token", "-"], input=token + "\n",
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    creds = json.loads((state / "credentials.json").read_text())
    return conf, state, creds


def go_run(go_agent, conf, log: Path):
    return subprocess.Popen([go_agent, "--config", conf, "run"], stdout=open(log, "w"), stderr=subprocess.STDOUT)


def test_go_agent_enrols_links_runs_jobs_and_stops_when_revoked(go_agent, tls_hub):
    conf, state, creds = go_enrol(go_agent, tls_hub)
    assert creds["url"] == tls_hub["url"] and creds["key"].startswith("lxa_") and "client_cert" not in creds
    for f in ("credentials.json", "client.pem"):
        assert os.stat(state / f).st_mode & 0o077 == 0
    a = db.session.get(Agent, creds["agent_id"])
    assert a.cert_sha256 and a.version and a.root == (os.geteuid() == 0)
    log = tls_hub["tmp"] / "go1.log"
    p = go_run(go_agent, conf, log)
    try:
        wait(lambda: agents.linked(db.session.get(Agent, creds["agent_id"])), timeout=20)
        view = tls_hub["admin"].get(f"/api/hosts/{DNS}/agent").get_json()["agent"]
        assert view["cert"]["on_link"] and view["facts"]["agent"] == "go" and view["host_allow"] == []
        for kind, params in (("ping", {"echo": "hi"}), ("facts", {})):
            j = tls_hub["admin"].post(f"/api/hosts/{DNS}/agent/jobs", json={"type": kind, "params": params}).get_json()
            j = job_done(tls_hub["admin"], j["id"])
            assert j["status"] == "succeeded", j
        assert j["result"]["kernel"]
        # metrics arrive with heartbeats (every second here)
        wait(lambda: (db.session.get(Agent, creds["agent_id"]).metrics or {}).get("mem_total"), timeout=10)
        # writes are off at both ends: the hub won't even queue one
        r = tls_hub["admin"].post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "patch.apply", "params": {}})
        assert r.status_code == 409
        assert subprocess.run([go_agent, "--config", conf, "health"], capture_output=True).returncode == 0
        audit = [json.loads(line) for line in (state / "audit.log").read_text().splitlines()]
        assert [e["event"] for e in audit].count("finished") == 2 and all(e["ok"] for e in audit if e["event"] == "finished")
        # a revoke closes the link, and the agent stops for good with exit code 3
        assert tls_hub["admin"].delete(f"/api/hosts/{DNS}/agent").status_code == 200
        assert p.wait(20) == 3, log.read_text()
    finally:
        if p.poll() is None:
            p.kill()


def test_go_agent_renews_its_certificate_over_the_link(go_agent, tls_hub):
    conf, state, creds = go_enrol(go_agent, tls_hub, "go2")
    old = creds and db.session.get(Agent, creds["agent_id"]).cert_sha256
    a = db.session.get(Agent, creds["agent_id"])
    a.cert_not_after = utcnow() + timedelta(days=10)  # runs out soon
    db.session.commit()
    log = tls_hub["tmp"] / "go2.log"
    p = go_run(go_agent, conf, log)
    try:
        end = time.time() + 20
        while "saved a new client certificate" not in log.read_text() and time.time() < end:
            time.sleep(0.1)
        assert "saved a new client certificate" in log.read_text(), log.read_text()
        a = db.session.get(Agent, creds["agent_id"])
        db.session.refresh(a)
        assert a.cert_sha256 != old and a.cert_prev_sha256 == old
    finally:
        p.terminate()
        assert p.wait(10) == 0


def test_go_agent_without_its_certificate_keeps_trying(go_agent, tls_hub):
    conf, state, creds = go_enrol(go_agent, tls_hub, "go3")
    os.remove(state / "client.pem")
    log = tls_hub["tmp"] / "go3.log"
    p = go_run(go_agent, conf, log)
    try:
        end = time.time() + 20
        while "client certificate" not in log.read_text() and time.time() < end:
            time.sleep(0.1)
        assert "client certificate" in log.read_text() and p.poll() is None, log.read_text()
    finally:
        p.terminate()
        p.wait(10)


def test_the_hub_serves_the_go_binaries_it_has(hub, loaded):
    """No Go needed: the hub serves whatever binaries are in LABDEX_AGENT_BIN_DIR, with their hashes
    in the enrolment answer, and offers no Go command without them."""
    import hashlib

    from .conftest import client
    bin_dir = hub["tmp"] / "bin"
    bin_dir.mkdir()
    loaded.config["LABDEX_AGENT_BIN_DIR"] = str(bin_dir)
    r = hub["admin"].post(f"/api/hosts/{DNS}/agent/token", json={}).get_json()
    # no Go binaries served: the Python agent is the command
    assert "go" not in r and r["agent"] == "python" and r["command"] == r["python"]["command"]
    assert "install.sh --python" in r["command"] and f"--sha256 {r['sha256']}" in r["command"]
    assert client(loaded).get("/api/agents/download/labdex-agent-linux-amd64").status_code == 404
    (bin_dir / "labdex-agent-linux-amd64").write_bytes(b"\x7fELF amd64")
    (bin_dir / "labdex-agent-linux-riscv64").write_bytes(b"not offered")
    sha = hashlib.sha256(b"\x7fELF amd64").hexdigest()
    r = hub["admin"].post(f"/api/hosts/{DNS}/agent/token", json={}).get_json()
    assert r["go"]["sha256"] == {"amd64": sha}
    assert f"--sha256 {sha}" in r["go"]["command"] and r["token"] in r["go"]["command"] and "--python" not in r["go"]["command"]
    # the Go agent is the default command once served, the Python one is still offered
    assert r["agent"] == "go" and r["command"] == r["go"]["command"]
    assert "install.sh --python" in r["python"]["command"] and r["token"] in r["python"]["command"]
    d = client(loaded).get("/api/agents/download/labdex-agent-linux-amd64")  # hosts need no login
    assert d.status_code == 200 and d.data == b"\x7fELF amd64" and d.headers["X-Labdex-SHA256"] == sha
    assert client(loaded).get("/api/agents/download/labdex-agent-linux-riscv64").status_code == 404
    # a rebuilt binary gets its new hash
    (bin_dir / "labdex-agent-linux-amd64").write_bytes(b"\x7fELF amd64 v2")
    r = hub["admin"].post(f"/api/hosts/{DNS}/agent/token", json={}).get_json()
    assert r["go"]["sha256"]["amd64"] == hashlib.sha256(b"\x7fELF amd64 v2").hexdigest()


def test_install_script_installs_go_by_default():
    text = (Path(__file__).resolve().parents[1] / "labdex" / "agent" / "dist" / "install.sh").read_text()
    assert "GO=1\n" in text and "--python) GO=0" in text and "--go) GO=1" in text and "labdex-agent-linux-$GOARCH" in text
    assert "ExecStart=/opt/labdex-agent/labdex-agent run" in text


def test_go_agent_tells_the_hub_its_ssh_users_and_runs_the_report(go_agent, tls_hub):
    conf, state, creds = go_enrol(go_agent, tls_hub, name="go-ssh")
    with open(conf, "a") as f:
        f.write("ssh_users = deploy, admin\n")
    p = go_run(go_agent, conf, tls_hub["tmp"] / "go-ssh.log")
    try:
        wait(lambda: agents.linked(db.session.get(Agent, creds["agent_id"])), timeout=20)
        a = db.session.get(Agent, creds["agent_id"])
        db.session.refresh(a)
        assert a.ssh_users == ["admin", "deploy"] and {"ssh_keys.report", "ssh_keys.apply"} <= set(a.jobs)
        j = tls_hub["admin"].post(f"/api/hosts/{DNS}/ssh-keys/report").get_json()
        j = job_done(tls_hub["admin"], j["id"])
        # here the root side isn't installed (/opt/labdex-agent): the job says so instead of hanging
        assert j["status"] in ("succeeded", "failed") and (j["status"] == "succeeded" or "ssh_keys_report" in j["error"]), j
    finally:
        p.kill()
