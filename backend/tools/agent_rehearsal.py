"""Rehearse the host agent's install in a throwaway systemd container, before you touch a real host.

    cd backend && python tools/agent_rehearsal.py [--image IMAGE] [--keep] [--agent python|go]

It starts a local labdex (gunicorn over TLS, client certificates required, the example inventory)
and a container with systemd that stands in for a host. Then it does what the guide *A first agent,
read-only* says: it fetches install.sh from labdex, installs read-only, and checks the service, the
link, the client certificate and the read-only jobs. It revokes the agent, which must stop for good,
and uninstalls with --purge, which must leave nothing behind.

With --agent go it installs the Go agent (agent/, built here with `go build` unless
LABDEX_AGENT_BIN_DIR already holds the binaries) through `install.sh --go`, and runs the same checks.

Needs Docker (privileged containers, cgroup v2), openssl, and this backend installed with
`.[server]`. The container shares the host's network to reach labdex on 127.0.0.1. Nothing outside
the container and a temporary folder is touched.

The default image, geerlingguy/docker-debian12-ansible, has systemd, python3 with cryptography,
sudo and Ansible, so the install needs no package downloads.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = "srv-vm-dns-01"  # in examples/homelab.yaml
USER, PASSWORD = "admin", "correct-horse-battery"


class Failed(Exception):
    pass


def sh(*argv, check=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(argv, capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise Failed(f"{' '.join(argv[:4])}… failed ({r.returncode}): {(r.stdout + r.stderr)[-800:]}")
    return r


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def go_binaries(work: Path) -> Path:
    """The folder with the Go agent for this machine's architecture: LABDEX_AGENT_BIN_DIR, or built here."""
    if os.environ.get("LABDEX_AGENT_BIN_DIR"):
        return Path(os.environ["LABDEX_AGENT_BIN_DIR"])
    out = work / "agent-bin"
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(os.uname().machine, os.uname().machine)
    sh("go", "build", "-trimpath", "-o", str(out / f"labdex-agent-linux-{arch}"), "./cmd/labdex-agent",
       cwd=ROOT / "agent", env=os.environ | {"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": arch})
    return out


class Hub:
    def __init__(self, work: Path, bin_dir: Path | None = None):
        self.work, self.data, self.port = work, work / "data", free_port()
        self.url = f"https://127.0.0.1:{self.port}"
        self.env = os.environ | {"LABDEX_DATA": str(self.data), "LABDEX_AGENT_MTLS": "required", "LABDEX_OFFICIAL_INDEX": "",
                                 "LABDEX_ADMIN_USER": USER, "LABDEX_ADMIN_PASSWORD": PASSWORD, "NO_PROXY": "127.0.0.1,localhost",
                                 "no_proxy": "127.0.0.1,localhost"}
        if bin_dir is not None:
            self.env["LABDEX_AGENT_BIN_DIR"] = str(bin_dir)
        self.proc = None
        self.cookies = None

    def start(self):
        seed = ("import sys; from pathlib import Path; from labdex import create_app, inventory, agent_pki\n"
                "app = create_app({'LABDEX_SCHEDULER': False})\n"
                "with app.app_context():\n"
                f"    inventory.import_yaml(Path({str(ROOT / 'examples' / 'homelab.yaml')!r}).read_text())\n"
                "    agent_pki.ca()\n")
        sh(sys.executable, "-c", seed, env=self.env, cwd=ROOT / "backend")
        sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
           "-keyout", str(self.work / "hub.key"), "-out", str(self.work / "hub.pem"), "-subj", "/CN=hub.example.com",
           "-addext", "subjectAltName=IP:127.0.0.1")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "--workers", "1", "--threads", "16", "--bind", f"127.0.0.1:{self.port}",
             "--certfile", str(self.work / "hub.pem"), "--keyfile", str(self.work / "hub.key"),
             "--cert-reqs", "1", "--ca-certs", str(self.data / "agent-ca.pem"), "labdex:create_app()"],
            env=self.env, cwd=ROOT / "backend", stdout=open(self.work / "hub.log", "w"), stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                self.call("GET", "/healthz")
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise Failed("labdex didn't start; see hub.log")
        self.call("POST", "/api/auth/login", {"username": USER, "password": PASSWORD})

    def call(self, method: str, path: str, body=None):
        import http.cookiejar
        import ssl
        if self.cookies is None:
            self.cookies = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context(cafile=str(self.work / "hub.pem"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx),
                                             urllib.request.HTTPCookieProcessor(self.cookies))
        req = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Requested-With": "labdex"})
        with opener.open(req, timeout=30) as r:
            text = r.read().decode()
        return json.loads(text) if text.startswith(("{", "[")) else text

    def job(self, kind: str, params=None, wait=120) -> dict:
        j = self.call("POST", f"/api/hosts/{HOST}/agent/jobs", {"type": kind, "params": params or {}})
        end = time.time() + wait
        while time.time() < end:
            j = self.call("GET", f"/api/agents/jobs/{j['id']}")
            if j["status"] not in ("queued", "sent", "running"):
                return j
            time.sleep(0.5)
        raise Failed(f"{kind} didn't finish in {wait} s")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)


class Box:
    """The stand-in host: a privileged container running systemd."""

    def __init__(self, image: str, work: Path):
        self.image, self.work, self.name = image, work, f"labdex-rehearsal-{os.getpid()}"

    def start(self):
        sh("docker", "run", "-d", "--name", self.name, "--hostname", "rehearsal", "--privileged", "--cgroupns=host",
           "--network", "host", "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw", "-v", f"{self.work}:/rehearsal:ro",
           "--tmpfs", "/run", "--tmpfs", "/run/lock", self.image)
        for _ in range(60):
            state = self.run("systemctl is-system-running", check=False).stdout.strip()
            if state in ("running", "degraded"):
                return
            time.sleep(1)
        raise Failed("systemd in the container didn't come up")

    def run(self, script: str, check=True) -> subprocess.CompletedProcess:
        return sh("docker", "exec", self.name, "sh", "-c", script, check=check)

    def remove(self):
        sh("docker", "rm", "-f", self.name, check=False)


def rehearse(image: str, keep: bool, agent: str = "python") -> list[str]:
    work = Path(tempfile.mkdtemp(prefix="labdex-rehearsal-"))
    work.chmod(0o755)
    hub, box = Hub(work, go_binaries(work) if agent == "go" else None), Box(image, work)
    passed: list[str] = []

    def ok(what: str):
        passed.append(what)
        print(f"  ok  {what}")

    try:
        hub.start()
        ok(f"labdex runs at {hub.url}, client certificates required")
        box.start()
        ok(f"systemd runs in {image}")
        t = hub.call("POST", f"/api/hosts/{HOST}/agent/token", {})
        if agent == "go":
            if not t.get("go"):
                raise Failed("labdex offers no Go agent: no binaries in LABDEX_AGENT_BIN_DIR")
            how = "--go " + " ".join(f"--sha256 {sha}" for sha in t["go"]["sha256"].values())
        else:
            how = f"--sha256 {t['sha256']}"
        fetch = ("python3 -c \"import ssl, urllib.request as u; open('/root/install.sh', 'wb').write(u.urlopen("
                 f"'{hub.url}/api/agents/install.sh', context=ssl.create_default_context(cafile='/rehearsal/hub.pem')).read())\"")
        out = box.run(f"{fetch} && sh /root/install.sh --url {hub.url} --token {t['token']} {how} "
                      "--ca-file /rehearsal/hub.pem").stdout
        if "running as labdex-agent" not in out:
            raise Failed(f"install.sh: {out[-500:]}")
        if agent == "go" and "/opt/labdex-agent/labdex-agent run" not in box.run("systemctl cat labdex-agent").stdout:
            raise Failed("install.sh --go didn't point the service at the Go agent")
        ok(f"install.sh installed the {agent} agent read-only, as labdex-agent")
        for _ in range(30):
            if box.run("labdex-agent health", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise Failed("labdex-agent health: " + box.run("journalctl -u labdex-agent -n 20 --no-pager", check=False).stdout)
        ok("the service is up and labdex answers")
        sudoers = box.run("cat /etc/sudoers.d/labdex-agent").stdout
        if "apt-get" in sudoers or "shutdown" in sudoers:
            raise Failed("write rules in a read-only install:\n" + sudoers)
        ok("the sudo rules are read-only")
        a = hub.call("GET", f"/api/hosts/{HOST}/agent")["agent"]
        if not (a["linked"] and a["cert"]["on_link"] and not a["root"] and not a["host_allow"]
                and (agent != "go" or a["facts"].get("agent") == "go")):
            raise Failed(f"agent state: {json.dumps(a)[:600]}")
        ok("linked with its client certificate")
        for kind in ("ping", "facts", "discovery.services", "patch.report"):
            j = hub.job(kind)
            if j["status"] != "succeeded":
                raise Failed(f"{kind}: {j['status']} {j['error']}")
            notes = (j.get("result") or {}).get("notes") or []
            if kind == "discovery.services" and notes:
                raise Failed(f"discovery notes: {notes}")
            ok(f"job {kind} succeeded")
        r = box.run("labdex-agent show discovery > /dev/null && echo shown", check=False)
        if "shown" in r.stdout:
            ok("labdex-agent show discovery works on the host")
        hub.call("DELETE", f"/api/hosts/{HOST}/agent")
        for _ in range(20):
            if box.run("systemctl is-active labdex-agent", check=False).stdout.strip() != "active":
                break
            time.sleep(0.5)
        status = box.run("systemctl show labdex-agent -p ExecMainStatus --value").stdout.strip()
        time.sleep(12)  # longer than RestartSec: it must not come back
        if status != "3" or box.run("systemctl is-active labdex-agent", check=False).stdout.strip() == "active":
            raise Failed(f"after revoke: exit status {status}, {box.run('systemctl is-active labdex-agent', check=False).stdout}")
        ok("revoke stopped it for good (exit status 3)")
        box.run("sh /root/install.sh --uninstall --purge")
        left = box.run("ls -d /opt/labdex-agent /etc/labdex-agent /var/lib/labdex-agent /etc/sudoers.d/labdex-agent "
                       "/etc/systemd/system/labdex-agent.service /usr/local/bin/labdex-agent 2>/dev/null; id labdex-agent 2>/dev/null",
                       check=False).stdout.strip()
        if left:
            raise Failed(f"left after --uninstall --purge: {left}")
        ok("--uninstall --purge left nothing")
        return passed
    finally:
        if keep:
            print(f"kept: container {box.name}, labdex at {hub.url} (pid {hub.proc.pid if hub.proc else '-'}), files in {work}")
        else:
            box.remove()
            hub.stop()
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--image", default="geerlingguy/docker-debian12-ansible:latest", help="a container image with systemd")
    ap.add_argument("--keep", action="store_true", help="leave the container and labdex running to look around")
    ap.add_argument("--agent", choices=["python", "go"], default="python", help="which agent to install (default: python)")
    args = ap.parse_args()
    for tool in ("docker", "openssl") + (("go",) if args.agent == "go" and not os.environ.get("LABDEX_AGENT_BIN_DIR") else ()):
        if not shutil.which(tool):
            print(f"needs {tool}", file=sys.stderr)
            return 2
    try:
        passed = rehearse(args.image, args.keep, args.agent)
    except Failed as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    print(f"all {len(passed)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
