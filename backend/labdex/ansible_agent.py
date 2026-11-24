"""Ansible through the agent: SSH certificates and inventory variables for hosts whose Go agent
carries the run's SSH connections (agent_pipes.py). Design: docs/develop/design/ansible-through-the-agent.md.

- **The SSH user CA** (Ed25519) lives in the vault at ``labdex/ansible-ssh-ca``, made on first use,
  so runs through the agent need the vault unsealed.
- **Per run, per host** labdex makes a fresh key and a certificate for ``labdex-ansible@<host>``,
  valid for the run (at most an hour) and only from ``127.0.0.1``/``::1``, so it works only through
  that host's agent. No extensions: no PTY, no forwarding. Both are removed with the run's folder.
- **Host keys** are pinned from the agent's facts (``ssh_host_keys``), never trusted on first use.
- **Become** stays off unless an administrator switched it on for the host (``ansible_become``);
  the host's own sudo rules (install.sh --ansible-become) are the other half.
"""
from __future__ import annotations

import secrets
import shutil
import sys
import time
from pathlib import Path

from flask import current_app

from . import agent_pipes, agents, audit, keyvault
from .models import Agent, VaultEntry, db

CA_PATH = "labdex/ansible-ssh-ca"
USER = "labdex-ansible"
CERT_MINUTES = 60
HOST_KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa")


class AnsibleAgentError(Exception):
    pass


# ----------------------------------------------------------------------------- the CA
def _ca_entry() -> VaultEntry | None:
    return db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == CA_PATH))


def ca_key(actor: str = "labdex"):
    """The CA's private key, made and stored in the vault the first time."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    try:
        e = _ca_entry()
        if e is not None:
            return serialization.load_ssh_private_key(keyvault.read(e)["private_key"].encode(), password=None)
        key = Ed25519PrivateKey.generate()
        priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                 serialization.NoEncryption()).decode()
        e = keyvault.create(CA_PATH, {"private_key": priv, "public_key": _public_line(key)}, actor,
                            description="SSH user CA for Ansible through the agent. Made by labdex; hosts trust its public key.")
        audit.record("create", "secret", e.path, None, {"path": e.path, "fields": ["private_key", "public_key"]},
                     summary="SSH user CA for Ansible through the agent made")
        db.session.commit()
        return key
    except keyvault.Sealed:
        raise AnsibleAgentError("the vault is sealed: Ansible through the agent needs the SSH CA from it; "
                                "an administrator unseals it") from None
    except keyvault.VaultError as e:
        raise AnsibleAgentError(f"the SSH CA: {e}") from None


def _public_line(key) -> str:
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode() + " labdex-ansible-ca"


def ca_public() -> str:
    """The line hosts put in /etc/ssh/labdex-ansible-ca.pub."""
    return _public_line(ca_key())


def certificate(ca, host: str, key_id: str, minutes: int = CERT_MINUTES) -> tuple[bytes, bytes]:
    """A fresh key and its certificate: (private key in OpenSSH PEM, certificate line)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import SSHCertificateBuilder, SSHCertificateType
    key = Ed25519PrivateKey.generate()
    now = int(time.time())
    cert = (SSHCertificateBuilder()
            .public_key(key.public_key())
            .serial(secrets.randbits(64))
            .type(SSHCertificateType.USER)
            .key_id(key_id.encode()[:200])
            .valid_principals([f"{USER}@{host}".encode()])
            .valid_after(now - 60)
            .valid_before(now + minutes * 60 + 60)
            # only from the host itself: through its agent's pipe, never from the network
            .add_critical_option(b"source-address", b"127.0.0.1/32,::1/128")
            .sign(ca))
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    return priv, cert.public_bytes() + b"\n"


# ----------------------------------------------------------------------------- which hosts
def through_agent(names) -> dict[str, Agent]:
    """The hosts of a run that go through their agent: a linked agent that runs ansible.session,
    with ansible switched on at both ends."""
    out = {}
    for name in names:
        a = agents.current(name)
        if (a is not None and a.revoked_at is None and "ansible.session" in (a.jobs or [])
                and "ansible" in (a.capabilities or []) and "ansible" in (a.host_allow or [])
                and agent_pipes.is_linked(a.id)):
            out[name] = a
    return out


def known_hosts_lines(host: str, a: Agent) -> list[str]:
    keys = [k for k in (a.facts or {}).get("ssh_host_keys") or [] if isinstance(k, str)]
    lines = []
    for k in keys:
        parts = k.split()
        if len(parts) == 2 and parts[0] in HOST_KEY_TYPES:
            lines.append(f"{host} {parts[0]} {parts[1]}")
    return lines


# ----------------------------------------------------------------------------- one run
def attach(inv: dict, run_dir: Path, by: str = "ansible", log=None):
    """Point the hosts of `inv` that go through their agent at the pipes, in place, and return the
    agent_pipes.Run to close when ansible-playbook is done (None when no host goes through an agent).
    A host whose agent reported no sshd host keys is left out of the run's pipes, and said so."""
    hostvars = inv.get("all", {}).get("hosts") or {}
    chosen = through_agent(list(hostvars))
    if not chosen:
        return None
    run_dir = Path(run_dir)
    pipe_dir = run_dir / "pipes"
    pipe_dir.mkdir(mode=0o700)
    client = pipe_dir / "pipe.py"
    shutil.copyfile(Path(__file__).with_name("agent_pipe_client.py"), client)
    ca = ca_key(by)
    known = []
    usable: dict[str, Agent] = {}
    for host, a in chosen.items():
        lines = known_hosts_lines(host, a)
        if not lines:
            if log:
                log(f"{host}: its agent reported no sshd host keys, so it can't go through the agent")
            continue
        known += lines
        usable[host] = a
    if not usable:
        return None
    (pipe_dir / "known_hosts").write_text("\n".join(known) + "\n")
    run = agent_pipes.Run(current_app._get_current_object(), pipe_dir, {h: a.id for h, a in usable.items()}, by=by)
    key_id = f"{run.session}/{by}"
    for host, a in usable.items():
        priv, cert = certificate(ca, host, f"{key_id}/{host}")
        keyfile = pipe_dir / f"{host}.key"
        _private(keyfile, priv)
        _private(Path(str(keyfile) + "-cert.pub"), cert)
        proxy = f"{sys.executable} -I {client} {run.path} {run.token} %n"
        hostvars[host] = (hostvars.get(host) or {}) | {
            "ansible_connection": "ssh",
            "ansible_host": host,  # the name only: the pipe decides where it goes
            "ansible_port": 22,
            "ansible_user": USER,
            "ansible_ssh_private_key_file": str(keyfile),
            "ansible_ssh_pipelining": True,
            "ansible_ssh_use_tty": False,
            "ansible_host_key_checking": True,
            "ansible_ssh_common_args": " ".join([
                f"-o ProxyCommand='{proxy}'",
                f"-o UserKnownHostsFile={pipe_dir / 'known_hosts'}",
                "-o GlobalKnownHostsFile=/dev/null",
                "-o StrictHostKeyChecking=yes",
                f"-o HostKeyAlias={host}",
                f"-o CertificateFile={keyfile}-cert.pub",
                "-o IdentitiesOnly=yes",
                "-o ForwardAgent=no",
            ]),
        }
        if not a.ansible_become:
            hostvars[host]["ansible_become"] = False
    if log:
        log(f"through the agent: {', '.join(sorted(usable))}")
    return run


def _private(path: Path, data: bytes) -> None:
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
