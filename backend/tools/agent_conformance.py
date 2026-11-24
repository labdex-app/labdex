"""Write agent/testdata/conformance.json: cases every agent must pass, taken from the Python agent.

    cd backend && python tools/agent_conformance.py          # rewrite the file
    cd backend && python tools/agent_conformance.py --check  # exit 1 when the file is out of date

The Go agent's tests read the file (agent/internal/...); tests/test_agent_conformance.py checks
that the Python agent still gives the same answers. See docs/develop/agent-protocol.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "agent" / "testdata" / "conformance.json"

# A fixed test key (never used anywhere else) so the signatures in the file are stable.
SEED = bytes(range(32))
AGENT = "1a2b3c4d"
NOW = 1764000000


def _signing() -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from labdex.agent import protocol

    key = Ed25519PrivateKey.from_private_bytes(SEED)
    pub = protocol.b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    def env(**over):
        e = {"v": 1, "id": "11111111-2222-3333-4444-555555555555", "agent": AGENT, "host": "srv-vm-dns-01",
             "type": "ping", "params": {"echo": "hi"}, "issued": NOW, "expires": NOW + 600, "by": "admin"}
        for k, v in over.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return e

    def signed(e):
        return protocol.sign(key, e)

    good = signed(env())
    other = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    raw_text = lambda text: {"t": "job", "job": text, "sig": protocol.b64(key.sign(text.encode()))}
    messages = {
        "valid": good,
        "valid with float times": signed(env(issued=NOW + 0.5, expires=NOW + 600.25)),
        "valid patch.apply with reboot": signed(env(type="patch.apply", params={"reboot": True})),
        "valid, issued within the skew": signed(env(issued=NOW + 299)),
        "valid, expired within the skew": signed(env(expires=NOW - 299)),
        "not a signed job: no sig": {"t": "job", "job": good["job"]},
        "not a signed job: job not text": {"t": "job", "job": {"v": 1}, "sig": good["sig"]},
        "signed by another key": protocol.sign(other, env()),
        "signature not base64": {"t": "job", "job": good["job"], "sig": "not base64!"},
        "text changed after signing": {"t": "job", "job": good["job"].replace("admin", "admim"), "sig": good["sig"]},
        "envelope version 2": signed(env(v=2)),
        "envelope not an object": raw_text("[1,2,3]"),
        "addressed to another agent": signed(env(agent="ffffffff")),
        "no times": signed(env(issued=None)),
        "times as text": signed(env(expires="soon")),
        "issued in the future": signed(env(issued=NOW + 301)),
        "expired": signed(env(expires=NOW - 301)),
        "unknown job type": signed(env(type="shell")),
        "no job type": signed(env(type=None)),
        "params not an object": signed(env(params=["x"])),
        "id not text": signed(env(id=7)),
    }
    cases = []
    for name, msg in messages.items():
        try:
            out = {"envelope": protocol.verify(pub, msg, AGENT, now=NOW)}
        except protocol.BadJob as e:
            out = {"error": str(e)}
        cases.append({"name": name, "message": msg, **out})
    caps = [{"type": t, "params": p, "needs": sorted(protocol.capabilities_for(t, p))}
            for t, p in [("ping", {}), ("facts", {}), ("patch.apply", {}), ("patch.apply", {"reboot": True}),
                         ("patch.apply", {"reboot": False}), ("ansible.run", {"bundle": "x"}), ("nope", {})]]
    return {"public_key": pub, "agent": AGENT, "now": NOW, "max_skew": protocol.MAX_SKEW, "cases": cases,
            "capabilities": caps, "job_types": {k: {"write": v["write"], "timeout": v["timeout"]} for k, v in protocol.JOB_TYPES.items()}}


REDACT = [
    ["mysqld", "--user=mysql", "--password", "hunter2", "--port", "3306"],
    ["app", "--token=abc", "--api-key", "k", "--apikey=v", "--ACCESS_KEY", "a", "-p", "x"],
    ["env", "DB_PASS=s3cret", "MYSQL_ROOT_PASSWORD=x", "auth:basic", "PATH=/usr/bin", "user=bob"],
    ["curl", "https://bob:pw@example.com/x", "postgres://u:p@db:5432/app", "https://example.com/a:b@c"],
    ["x", "--secret"],
    ["x", "--client-secret-file", "/etc/s", "--credentials=/c.json", "--private_key", "k"],
    ["redis-server", "*:6379"],
    ["java", "-Dpassword=x", "-jar", "a.jar"],
    ["nginx: master process /usr/sbin/nginx"],
]

SS = """\
Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
tcp   LISTEN 0      4096         0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=812,fd=3))
tcp   LISTEN 0      4096            [::]:22            [::]:*    users:(("sshd",pid=812,fd=4))
udp   UNCONN 0      0            0.0.0.0:53         0.0.0.0:*    users:(("dnsd",pid=900,fd=5),("dnsd",pid=900,fd=6))
tcp   LISTEN 0      511        127.0.0.1:6379       0.0.0.0:*    users:(("redis-server",pid=1001,fd=6))
tcp   LISTEN 0      4096         0.0.0.0:5380       0.0.0.0:*    users:(("docker-proxy",pid=1500,fd=4))
tcp   LISTEN 0      4096   127.0.0.53%lo:53         0.0.0.0:*    users:(("systemd-resolve",pid=600,fd=14))
tcp   LISTEN 0      128                *:8080             *:*    users:(("java",pid=2000,fd=9),("java",pid=1999,fd=9))
tcp   LISTEN 0      128    [fe80::1%eth0]:9000            *:*
tcp   ESTAB  0      0          10.0.0.5:22         10.0.0.9:51234 users:(("sshd",pid=3000,fd=4))
udp   UNCONN 0      0       0.0.0.0:bad        0.0.0.0:*
raw   UNCONN 0      0            0.0.0.0:1          0.0.0.0:*
tcp   LISTEN
"""

SHOW = """Id=ssh.service
Description=OpenBSD Secure Shell server
MainPID=812
User=
FragmentPath=/lib/systemd/system/ssh.service
ActiveEnterTimestamp=Mon 2026-11-16 08:00:00 UTC

Id=dnsd.service
Description=DNS = names
MainPID=0
User=dns
FragmentPath=/etc/systemd/system/dnsd.service
ActiveEnterTimestamp=

Description=no id here
"""

DOCKER = "\n".join([
    json.dumps({"ID": "0123456789abcdef", "Names": "technitium", "Image": "technitium/dns-server:13", "Status": "Up 2 days",
                "Labels": "com.docker.compose.project=dns,other=x", "Ports": "0.0.0.0:5380->5380/tcp, [::]:5380->5380/tcp"}),
    "not json",
    json.dumps({"ID": "abc", "Names": "web", "Image": "nginx", "Status": "Up", "Labels": "", "Ports": "8000-8001->80-81/tcp, 53/udp"}),
])

PODMAN = json.dumps([
    {"Id": "fedcba9876543210", "Names": ["db"], "Image": "postgres:17", "State": "running", "Pid": 4242,
     "Labels": {"com.docker.compose.project": "app"},
     "Ports": [{"host_ip": "", "host_port": 5432, "container_port": 5432, "protocol": "tcp", "range": 2}]},
    {"Id": "x", "Names": [], "Image": "busybox", "Status": "Exited", "Ports": None, "Labels": None},
])

CGROUPS = [
    "0::/system.slice/ssh.service\n",
    "0::/user.slice/user-1000.slice/session-3.scope\n",
    "0::/system.slice/docker-0123.scope/init.scope\n",
    "12:pids:/system.slice/nginx.service/worker\n1:name=systemd:/system.slice/nginx.service\n",
    "0::/\n",
    "",
]

APT = """\
Reading package lists...
Inst openssl [3.0.14-1~deb12u2] (3.0.15-1~deb12u1 Debian-Security:12/stable-security [amd64])
Inst libssl3 [3.0.14-1~deb12u2] (3.0.15-1~deb12u1 Debian:12.8/stable [amd64]) []
Inst tzdata (2024b-0+deb12u1 Debian:12.8/stable-updates [all])
Inst base-files [12.4+deb12u7] (12.4+deb12u8 Ubuntu:24.04/noble-security [amd64])
Conf openssl (3.0.15-1~deb12u1 Debian-Security:12/stable-security [amd64])
"""

DNF = """\
Last metadata expiration check: 0:10:00 ago on Mon 24 Nov 2026 08:00:00 AM UTC.

openssl-libs.x86_64          1:3.0.7-28.el9        baseos
kernel.x86_64                5.14.0-503.el9        baseos
python3.noarch               3.9.19-8.el9          appstream
Obsoleting Packages
foo                          1                     x
"""
DNF_SEC = """\
RHSA-2026:1234 Important/Sec. openssl-libs-1:3.0.7-28.el9.x86_64
RHSA-2026:1300 Moderate/Sec.  kernel-5.14.0-503.el9.x86_64
"""

APT_UPDATE = [
    "Hit:1 http://deb.debian.org/debian bookworm InRelease\nReading package lists...\n",
    "Err:2 http://deb.debian.org/debian bookworm InRelease\n  Temporary failure resolving\n",
    "W: Failed to fetch http://deb.debian.org/debian/dists/bookworm/InRelease\n",
    "W: Some index files failed to download. They have been ignored.\n",
    "E: Could not get lock\n",
    "W: no Release file\n",
]

ANSIBLE_PARAMS = [
    {"bundle": "a" * 64, "playbook": "ansible/site.yml"},
    {"bundle": "a" * 64, "playbook": "site.yaml", "commit": "abc", "vars": {"x": 1}, "check": True},
    {"bundle": "a" * 64, "playbook": "ansible/site.yml", "shell": "rm -rf /"},
    {"bundle": "A" * 64, "playbook": "site.yml"},
    {"bundle": "a" * 63, "playbook": "site.yml"},
    {"bundle": "a" * 64, "playbook": "../site.yml"},
    {"bundle": "a" * 64, "playbook": "ansible/../../site.yml"},
    {"bundle": "a" * 64, "playbook": "/etc/site.yml"},
    {"bundle": "a" * 64, "playbook": "site.json"},
    {"bundle": "a" * 64, "playbook": "site.yml", "vars": ["x"]},
    {"playbook": "site.yml"},
]


def build() -> dict:
    from labdex.agent import ansible, discovery, jobs, patch_report
    from labdex.agent.protocol import BadJob

    def ansible_case(p):
        try:
            ansible.check_params(p)
            return {"params": p, "error": ""}
        except BadJob as e:
            return {"params": p, "error": str(e)}

    return {
        "about": "Cases every labdex agent must pass. Generated by backend/tools/agent_conformance.py from the "
                 "Python agent; see docs/develop/agent-protocol.md. Don't edit by hand.",
        "signing": _signing(),
        "redact": [{"argv": a, "out": discovery.redact(a)} for a in REDACT],
        "ss": {"text": SS, "out": discovery.parse_ss(SS)},
        "systemctl_show": {"text": SHOW, "out": discovery.parse_show(SHOW)},
        "docker": {"text": DOCKER, "out": discovery.parse_docker(DOCKER)},
        "podman": {"text": PODMAN, "out": discovery.parse_podman(PODMAN)},
        "cgroup_unit": [{"text": t, "unit": discovery.unit_of_cgroup(t)} for t in CGROUPS],
        "apt": {"text": APT, "out": patch_report.parse_apt(APT)},
        "dnf": {"text": DNF, "security": DNF_SEC, "out": patch_report.parse_dnf(DNF, DNF_SEC)},
        "apt_fetch_failed": [{"text": t, "failed": jobs._apt_fetch_failed(t)} for t in APT_UPDATE],
        "ansible_params": [ansible_case(p) for p in ANSIBLE_PARAMS],
    }


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the file is out of date")
    args = ap.parse_args()
    text = render()
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} is out of date: run python tools/agent_conformance.py", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
