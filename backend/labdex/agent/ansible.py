"""ansible.run: a playbook from git, run on this host only, as a signed bundle.

The hub never sends a command or a playbook text in the job: the signed job names a bundle by its
SHA-256 (a .tar.gz the hub built from the site's git repository at a commit), the playbook inside
it and the variables. The agent fetches the bundle over the same link, checks the hash against the
signed one, unpacks it into its state folder (no absolute paths, no `..`, no links) and runs
`ansible-playbook -i localhost, -c local` through systemd-run, outside its own sandbox.

Ansible runs as root to do anything useful, so this job needs the agent to run as root (the
per-host opt-in) as well as the ansible capability at both ends.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile

from .protocol import BadJob

SHA256 = re.compile(r"^[0-9a-f]{64}$")
PLAYBOOK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.ya?ml$")
MAX_BUNDLE = 8 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024


def check_params(params: dict) -> None:
    unknown = set(params) - {"bundle", "playbook", "commit", "vars", "check"}
    if unknown:
        raise BadJob(f"unknown parameters: {', '.join(sorted(unknown))}")
    if not SHA256.match(str(params.get("bundle", ""))):
        raise BadJob("bundle must be a SHA-256")
    pb = str(params.get("playbook", ""))
    if not PLAYBOOK.match(pb) or ".." in pb.split("/"):
        raise BadJob("playbook must be a relative .yml path inside the bundle")
    if not isinstance(params.get("vars", {}), dict):
        raise BadJob("vars must be an object")


def unpack(data: bytes, dest: str) -> None:
    """Unpack a bundle safely: regular files and folders only, all inside dest."""
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            name = m.name.lstrip("./") if m.name.startswith("./") else m.name
            if name.startswith("/") or ".." in name.split("/") or not (m.isfile() or m.isdir()):
                raise BadJob(f"bundle holds a path it may not: {m.name}")
            total += m.size
            if total > MAX_UNPACKED:
                raise BadJob("bundle too large unpacked")
        root = os.path.realpath(dest)
        for m in members:
            target = os.path.realpath(os.path.join(dest, m.name))
            if target != root and not target.startswith(root + os.sep):
                raise BadJob(f"bundle path leaves its folder: {m.name}")
            if m.isdir():
                os.makedirs(target, mode=0o700, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            src = tar.extractfile(m)
            with open(target, "wb") as f:
                shutil.copyfileobj(src, f)
            os.chmod(target, 0o600)


def run(params: dict, ctx) -> dict:
    check_params(params)
    if not ctx.runner.root:
        raise BadJob("ansible.run needs the agent to run as root on this host (install with --root)")
    if not ctx.runner.has("ansible-playbook"):
        raise BadJob("ansible-playbook is not installed on this host")
    if ctx.fetch_bundle is None:
        raise BadJob("no link to fetch the bundle over")
    sha = params["bundle"]
    data = ctx.fetch_bundle(sha)
    if len(data) > MAX_BUNDLE:
        raise BadJob("bundle too large")
    if hashlib.sha256(data).hexdigest() != sha:
        raise BadJob("bundle does not match the signed hash")
    work = os.path.join(ctx.state_dir, "bundles", sha)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, mode=0o700)
    try:
        unpack(data, work)
        playbook = os.path.join(work, params["playbook"])
        if not os.path.isfile(playbook):
            raise BadJob(f"{params['playbook']} is not in the bundle")
        varfile = os.path.join(work, ".labdex-vars.json")
        with open(varfile, "w", encoding="utf-8") as f:
            json.dump(params.get("vars") or {}, f)
        argv = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--unit=labdex-agent-ansible",
                f"--working-directory={work}", "--setenv=ANSIBLE_NOCOLOR=1", "--setenv=ANSIBLE_RETRY_FILES_ENABLED=0",
                "ansible-playbook", "-i", "localhost,", "-c", "local", "-e", f"@{varfile}",
                *(["--check", "--diff"] if params.get("check") else []), playbook]
        with ctx.runner.write_lock:
            code, out, err = ctx.runner.run(argv, timeout=3300)
        recap = out[out.rfind("PLAY RECAP"):] if "PLAY RECAP" in out else ""
        return {"ok": code == 0, "code": code, "check": bool(params.get("check")), "commit": params.get("commit", ""),
                "recap": recap[:2000], "output": (out + err)[-20000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
