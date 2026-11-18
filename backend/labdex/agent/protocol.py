"""What the agent and the hub agree on: headers, message shapes, the job allowlist and job signing.

Shared by both sides, so it imports nothing from labdex (the agent runs on hosts without it).

A job travels as ``{"t": "job", "job": "<JSON text>", "sig": "<base64 Ed25519 signature>"}``. The
signature covers the exact text, so neither side has to agree on how to serialise JSON. The hub
signs with its own key; the agent pinned the public half when it enrolled and checks every job:
signature, addressed to this agent, not expired, not seen before, a known job type, and, for a job
that changes the host, a capability switched on in the agent's own config.
"""
from __future__ import annotations

import base64
import json
import time

PROTOCOL = 1
KEY_HEADER = "Labdex-Agent-Key"
VERSION_HEADER = "Labdex-Agent-Version"
PROTOCOL_HEADER = "Labdex-Agent-Protocol"
KEY_PREFIX = "lxa_"

HEARTBEAT = 30          # seconds between heartbeats (with metrics)
MAX_SKEW = 300          # seconds a job's times may be off between hub and host clocks
JOB_TTL = 600           # a job not started by then is dropped: the agent was away too long
MAX_MESSAGE = 16 * 1024 * 1024

# Close codes (4000-4999 are ours): the agent stops on these instead of reconnecting.
CLOSE_REFUSED = 4401    # unknown or revoked key
CLOSE_ROOT = 4403       # runs as root, and the host isn't allowed a root agent
CLOSE_PROTOCOL = 4426   # one side has to be updated
CLOSE_CERT = 4495       # no client certificate, or the wrong one: try again later (a proxy may be fixed)
FATAL_CLOSES = {CLOSE_REFUSED, CLOSE_ROOT, CLOSE_PROTOCOL}

# Every job type there is. Nothing else runs, and there is no way to pass a shell command.
# write: None for read-only jobs; else the capability that must be on at both ends.
JOB_TYPES: dict[str, dict] = {
    "ping": {"write": None, "timeout": 10, "about": "answer, to test the link"},
    "facts": {"write": None, "timeout": 30, "about": "OS, kernel, CPU, memory, disks"},
    "discovery.services": {"write": None, "timeout": 120,
                           "about": "listening ports and the processes behind them, running systemd units, running containers"},
    "patch.report": {"write": None, "timeout": 600, "about": "updates waiting and whether a reboot is needed"},
    "patch.apply": {"write": "patch", "timeout": 3600, "about": "install the waiting updates (a reboot needs the reboot capability too)"},
    "ansible.run": {"write": "ansible", "timeout": 3600, "about": "run a signed playbook bundle from git on this host"},
}
CAPABILITIES = ("patch", "reboot", "ansible")


class BadJob(ValueError):
    """A job the agent won't run; the text says why (it goes to both audit logs)."""


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode(), validate=True)


def capabilities_for(job_type: str, params: dict) -> set[str]:
    """The write capabilities a job needs, from its type and parameters."""
    spec = JOB_TYPES.get(job_type) or {}
    need = {spec["write"]} if spec.get("write") else set()
    if job_type == "patch.apply" and params.get("reboot"):
        need.add("reboot")
    return need


def sign(private_key, envelope: dict) -> dict:
    """The hub side: the job message for one envelope (cryptography Ed25519PrivateKey)."""
    text = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    return {"t": "job", "job": text, "sig": b64(private_key.sign(text.encode()))}


def verify(public_key_b64: str, message: dict, agent_id: str, now: float | None = None) -> dict:
    """The agent side: the envelope of a job message, once its signature, address and times check out."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    text, sig = message.get("job"), message.get("sig")
    if not isinstance(text, str) or not isinstance(sig, str):
        raise BadJob("not a signed job")
    try:
        Ed25519PublicKey.from_public_bytes(unb64(public_key_b64)).verify(unb64(sig), text.encode())
    except (InvalidSignature, ValueError) as e:
        raise BadJob("bad signature") from e
    env = json.loads(text)
    if not isinstance(env, dict) or env.get("v") != 1:
        raise BadJob("unknown job format")
    if env.get("agent") != agent_id:
        raise BadJob("addressed to another agent")
    now = time.time() if now is None else now
    issued, expires = env.get("issued"), env.get("expires")
    if not isinstance(issued, (int, float)) or not isinstance(expires, (int, float)):
        raise BadJob("job without times")
    if issued > now + MAX_SKEW:
        raise BadJob("issued in the future: check the clocks")
    if expires < now - MAX_SKEW:
        raise BadJob("expired")
    if env.get("type") not in JOB_TYPES:
        raise BadJob(f"unknown job type {env.get('type')!r}")
    if not isinstance(env.get("params"), dict) or not isinstance(env.get("id"), str):
        raise BadJob("job without id or params")
    return env
