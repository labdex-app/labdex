#!/usr/bin/env python3
"""labdex-patch-report: tell labdex which updates this host has waiting and whether it needs a reboot.

Runs on the host (Debian, Ubuntu, Proxmox VE, Fedora, RHEL and friends), from cron, a systemd timer
or Ansible. Only the Python 3 standard library, so it works on a bare VM. It reads what the package
manager already knows; with --refresh it updates the package lists first (needs root).

    LABDEX_URL=https://labdex.example.net LABDEX_TOKEN=... labdex-patch-report
    labdex-patch-report --url https://labdex.example.net --token-file /etc/labdex/token --refresh
    labdex-patch-report --dry-run        # print the report, send nothing

The token is an API token of a user who may edit the host (Settings › API tokens).
Get this file from your labdex: GET /api/patches/agent.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

APT_INST = re.compile(r"^Inst (\S+) (?:\[(\S+)\] )?\((\S+) ([^)]*)\)")


def run(*cmd: str) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, r.stdout


def parse_apt(text: str) -> list[dict]:
    """`apt-get -s dist-upgrade` → packages. A package from a *-security pocket is a security update."""
    out = []
    for line in text.splitlines():
        m = APT_INST.match(line)
        if m:
            name, old, new, where = m.groups()
            out.append({"name": name, "from": old or "", "to": new, "security": "-security" in where or "Debian-Security" in where})
    return out


def _rpm_name(nevra: str) -> str:
    """openssl-libs-1:3.0.7-27.el9.x86_64 → openssl-libs"""
    return nevra.rsplit("-", 2)[0] if nevra.count("-") >= 2 else nevra


def parse_dnf(check_update: str, security: str = "") -> list[dict]:
    """`dnf check-update` (+ `dnf updateinfo list --security`) → packages."""
    sec = {_rpm_name(f[-1]) for f in (line.split() for line in security.splitlines()) if len(f) >= 3}
    out = []
    for line in check_update.splitlines():
        f = line.split()
        if len(f) != 3 or "." not in f[0] or line.startswith(("Last metadata", "Obsoleting", " ")):
            continue
        name = f[0].rsplit(".", 1)[0]
        out.append({"name": name, "from": "", "to": f[1], "security": name in sec})
    return out


def apt_patched_at(log: str = "/var/log/apt/history.log") -> str | None:
    """When apt last upgraded something: the Start-Date of the last entry with an Upgrade: line."""
    when = last = None
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Start-Date:"):
                    when = line.split(":", 1)[1].strip()
                elif line.startswith("Upgrade:") and when:
                    last = when
    except OSError:
        return None
    if not last:
        return None
    try:
        return datetime.strptime(last, "%Y-%m-%d  %H:%M:%S").astimezone().isoformat()
    except ValueError:
        return None


def rpm_patched_at() -> str | None:
    code, out = run("rpm", "-qa", "--qf", "%{INSTALLTIME}\n")
    stamps = [int(x) for x in out.split() if x.isdigit()]
    return datetime.fromtimestamp(max(stamps), timezone.utc).isoformat() if code == 0 and stamps else None  # noqa: UP017 (hosts may run Python < 3.11)


def newest_kernel_differs() -> bool:
    """Proxmox and some Debian setups don't write reboot-required for a new kernel: compare with /boot."""
    running = os.uname().release
    installed = [p.split("vmlinuz-", 1)[1] for p in glob.glob("/boot/vmlinuz-*")]
    if not installed or running not in installed:
        return False

    def key(v):
        return [int(x) if x.isdigit() else x for x in re.split(r"[.-]", v)]
    try:
        return max(installed, key=key) != running
    except TypeError:
        return False


def os_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            info = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
        name = info.get("PRETTY_NAME", "").strip('"')
    except OSError:
        name = ""
    if shutil.which("pveversion"):
        code, out = run("pveversion")
        if code == 0 and out.strip():
            name = out.split()[0].replace("pve-manager/", "Proxmox VE ").split("/")[0] + (f" ({name})" if name else "")
    return name


def collect(refresh: bool = False) -> dict:
    report = {"os": os_name(), "kernel": os.uname().release}
    if shutil.which("apt-get"):
        if refresh:
            run("apt-get", "-qq", "update")
        code, out = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade")
        if code != 0:
            report["error"] = "apt-get -s dist-upgrade failed"
        report["packages"] = parse_apt(out)
        report["reboot"] = os.path.exists("/var/run/reboot-required") or (bool(shutil.which("pveversion")) and newest_kernel_differs())
        report["patched_at"] = apt_patched_at()
    elif shutil.which("dnf") or shutil.which("yum"):
        dnf = "dnf" if shutil.which("dnf") else "yum"
        code, out = run(dnf, "-q", *([] if refresh else ["--cacheonly"]), "check-update")
        if code not in (0, 100):
            report["error"] = f"{dnf} check-update failed"
        _, sec = run(dnf, "-q", "--cacheonly", "updateinfo", "list", "--security", "--available")
        report["packages"] = parse_dnf(out, sec)
        code, _ = run("needs-restarting", "-r")
        report["reboot"] = code == 1
        report["patched_at"] = rpm_patched_at()
    else:
        report["error"] = "no apt-get, dnf or yum here"
        report["packages"] = []
    report["pending"] = len(report["packages"])
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="labdex-patch-report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("LABDEX_URL", ""), help="labdex address (LABDEX_URL)")
    ap.add_argument("--token-file", help="a file with the API token (else LABDEX_TOKEN)")
    ap.add_argument("--host", default=socket.gethostname().split(".")[0], help="the host's name in labdex (default: short hostname)")
    ap.add_argument("--source", default="agent", help="name of this report's source (default: agent)")
    ap.add_argument("--refresh", action="store_true", help="update the package lists first (needs root)")
    ap.add_argument("--ca-file", help="CA bundle for a labdex with a private certificate")
    ap.add_argument("--dry-run", action="store_true", help="print the report and send nothing")
    a = ap.parse_args(argv)
    report = {"host": a.host, "source": a.source, **collect(a.refresh)}
    if a.dry_run:
        print(json.dumps(report, indent=2))
        return 0
    token = os.environ.get("LABDEX_TOKEN", "")
    if a.token_file:
        with open(a.token_file, encoding="utf-8") as f:
            token = f.read().strip()
    if not a.url or not token:
        print("labdex-patch-report: set --url (LABDEX_URL) and a token (LABDEX_TOKEN or --token-file)", file=sys.stderr)
        return 2
    req = urllib.request.Request(a.url.rstrip("/") + "/api/patches", data=json.dumps(report).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context(cafile=a.ca_file)) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"labdex-patch-report: {e.code} {e.read().decode(errors='replace')[:300]}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"labdex-patch-report: {e}", file=sys.stderr)
        return 1
    if a.host not in out.get("saved", []):
        print(f"labdex-patch-report: labdex has no host '{a.host}' (use --host)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
