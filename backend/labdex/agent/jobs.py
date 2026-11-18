"""The job types the agent runs, and the only commands it may run with privileges.

There is no shell anywhere here: a job is one of protocol.JOB_TYPES with checked parameters, and a
privileged step is one of the fixed command lines in PRIVILEGED. `labdex-agent sudoers` prints the
sudo rules for exactly those lines (read-only ones always, write ones only for capabilities the
host switched on), so the rules and the code can't drift apart.

Write jobs run through `systemd-run --wait --pipe`: outside the agent's own sandbox (its
ProtectSystem=strict would make /usr read-only for apt as well), one at a time, logged in the
journal under a fixed unit name.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading

from .protocol import BadJob

SYSTEMD_RUN = ("systemd-run", "--quiet", "--wait", "--pipe", "--collect")
APT_OPTS = ("-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold")

# name -> (capability or None for read-only, argv). argv[0] is looked up on PATH on this host.
PRIVILEGED: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "ss": (None, ("ss", "-Htulpn")),
    "docker_ps": (None, ("docker", "ps", "--no-trunc", "--format", "{{json .}}")),
    "podman_ps": (None, ("podman", "ps", "--format", "json")),
    "apt_update": ("patch", (*SYSTEMD_RUN, "--unit=labdex-agent-patch", "--setenv=DEBIAN_FRONTEND=noninteractive",
                             "apt-get", "-q", "update")),
    "apt_upgrade": ("patch", (*SYSTEMD_RUN, "--unit=labdex-agent-patch", "--setenv=DEBIAN_FRONTEND=noninteractive",
                              "apt-get", "-q", "-y", *APT_OPTS, "dist-upgrade")),
    "dnf_upgrade": ("patch", (*SYSTEMD_RUN, "--unit=labdex-agent-patch", "dnf", "-q", "-y", "upgrade")),
    "reboot": ("reboot", ("shutdown", "-r", "+1", "labdex-agent: reboot after updates")),
}
_SUDOERS_SPECIAL = str.maketrans({c: "\\" + c for c in ",:=\\"})


def _resolve(argv: tuple[str, ...]) -> list[str] | None:
    """Absolute paths for the program and, for systemd-run, the program it starts."""
    out = list(argv)
    first = shutil.which(out[0], path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if not first:
        return None
    out[0] = first
    if argv[0] == "systemd-run":
        i = next(i for i, a in enumerate(out) if i > 0 and not a.startswith("-"))
        prog = shutil.which(out[i], path="/usr/sbin:/usr/bin:/sbin:/bin")
        if not prog:
            return None
        out[i] = prog
    return out


def sudoers(user: str, allow: set[str]) -> str:
    """The sudo rules for this host: every read-only line, and the write lines of switched-on capabilities."""
    lines = ["# labdex-agent: written by `labdex-agent sudoers`; the agent runs nothing else with sudo.",
             f"Defaults:{user} !requiretty, !lecture"]
    for name, (cap, argv) in PRIVILEGED.items():
        if cap is not None and cap not in allow:
            continue
        full = _resolve(argv)
        if full is None:
            lines.append(f"# {name}: {argv[0]} is not installed here")
            continue
        lines.append(f"{user} ALL=(root) NOPASSWD: " + " ".join(a.translate(_SUDOERS_SPECIAL) for a in full))
    return "\n".join(lines) + "\n"


class Runner:
    """Runs commands for jobs. Tests swap it for a fake."""

    def __init__(self, root: bool | None = None):
        self.root = os.geteuid() == 0 if root is None else root
        self.write_lock = threading.Lock()  # write jobs run one at a time

    def run(self, argv: list[str], timeout: float = 60) -> tuple[int, str, str]:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]}: timed out after {timeout:.0f} s"
        except OSError as e:
            return 126, "", str(e)
        return r.returncode, r.stdout, r.stderr

    def privileged(self, name: str, timeout: float = 60) -> tuple[int, str, str]:
        """One of the PRIVILEGED lines: directly as root, else through `sudo -n` (never asks for a password)."""
        full = _resolve(PRIVILEGED[name][1])
        if full is None:
            return 127, "", f"{PRIVILEGED[name][1][0]}: not installed"
        return self.run(full if self.root else ["sudo", "-n", *full], timeout)

    def has(self, program: str) -> bool:
        return shutil.which(program, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin") is not None


class Context:
    """What a job handler gets: the runner, the agent's config and a way to ask the hub for a bundle."""

    def __init__(self, runner: Runner, allow: set[str], state_dir: str, fetch_bundle=None):
        self.runner = runner
        self.allow = allow
        self.state_dir = state_dir
        self.fetch_bundle = fetch_bundle


# ----------------------------------------------------------------------------- handlers
def job_ping(params: dict, ctx: Context) -> dict:
    import time
    return {"pong": True, "time": time.time(), "echo": str(params.get("echo", ""))[:200]}


def job_facts(params: dict, ctx: Context) -> dict:
    from . import host
    return host.facts()


def job_discovery(params: dict, ctx: Context) -> dict:
    from . import discovery
    return discovery.collect(ctx.runner)


def job_patch_report(params: dict, ctx: Context) -> dict:
    from . import patch_report
    # never --refresh here: updating the package lists needs root; patch.apply does it
    return patch_report.collect(refresh=False)


def job_patch_apply(params: dict, ctx: Context) -> dict:
    from . import patch_report
    unknown = set(params) - {"reboot"}
    if unknown:
        raise BadJob(f"unknown parameters: {', '.join(sorted(unknown))}")
    with ctx.runner.write_lock:
        steps = []
        if ctx.runner.has("apt-get"):
            names = ["apt_update", "apt_upgrade"]
        elif ctx.runner.has("dnf"):
            names = ["dnf_upgrade"]
        else:
            raise BadJob("no apt-get or dnf on this host")
        for name in names:
            code, out, err = ctx.runner.privileged(name, timeout=3000)
            steps.append({"step": name, "code": code, "output": (out + err)[-4000:]})
            if code != 0:
                return {"ok": False, "steps": steps, "error": f"{name} failed ({code})"}
        after = patch_report.collect(refresh=False)
        rebooting = False
        if params.get("reboot") and after.get("reboot"):
            code, out, err = ctx.runner.privileged("reboot")
            steps.append({"step": "reboot", "code": code, "output": (out + err)[-4000:]})
            rebooting = code == 0
        return {"ok": True, "steps": steps, "report": after, "rebooting": rebooting}


def job_ansible_run(params: dict, ctx: Context) -> dict:
    from . import ansible
    return ansible.run(params, ctx)


HANDLERS = {
    "ping": job_ping,
    "facts": job_facts,
    "discovery.services": job_discovery,
    "patch.report": job_patch_report,
    "patch.apply": job_patch_apply,
    "ansible.run": job_ansible_run,
}
