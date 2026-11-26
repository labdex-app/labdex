"""SSH login keys through the host agents (the Go agent; docs/guide/ssh-keys.md).

- **Keys** are people's public keys (``SSHKey``); labdex never holds a private key it didn't make
  for a break-glass key, and that one it shows once and forgets.
- **Grants** say which key may log in as which user on which host (``*``: every host whose agent
  manages that user).
- **On the host** labdex never edits ``~/.ssh/authorized_keys``. The agent writes root-owned
  ``/etc/ssh/labdex/<user>.keys`` and ``<user>.break-glass``, only for the users the host's
  ``agent.conf`` names in ``ssh_users``, and only with ``ssh_keys`` switched on at both ends.
- **Plan, then apply:** ``status()`` compares what labdex wants with the agent's last
  ``ssh_keys.report``; ``apply()`` queues ``ssh_keys.apply`` with the whole wanted set. Only
  administrators change keys, grants and break-glass keys, and every change is audited.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct

from . import agents, audit, store
from .models import Agent, SSHBreakGlass, SSHKey, SSHKeyGrant, db, utcnow

# The same rules as the Go agent (agent/internal/sshkeys/keys.go): it checks again on the host.
TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
         "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com", "ssh-rsa")
MIN_RSA_BITS = 3072
MAX_COMMENT = 100
MAX_KEYS_PER_USER = 200
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
COMMENT_RE = re.compile(r"^[A-Za-z0-9 @._+:=/()-]*$")
ALL_HOSTS = "*"


class SSHKeyError(ValueError):
    """A key or a request that can't be used; the text says why (shown as is)."""


def _read_string(blob: bytes, at: int) -> tuple[bytes, int]:
    if at + 4 > len(blob):
        raise ValueError("short")
    (n,) = struct.unpack(">I", blob[at:at + 4])
    if at + 4 + n > len(blob):
        raise ValueError("short")
    return blob[at + 4:at + 4 + n], at + 4 + n


def parse(line: str) -> dict:
    """``<type> <base64> [comment]``, never with options in front (command=, from=, …): those would
    let a key do more than labdex shows. Returns type, comment, sha256, bits (RSA) and the line."""
    line = (line or "").strip()
    if any(c in line for c in "\r\n\x00"):
        raise SSHKeyError("one key per line")
    f = line.split()
    if len(f) < 2:
        raise SSHKeyError('not a public key: want "<type> <base64> [comment]"')
    if f[0] not in TYPES:
        raise SSHKeyError(f"key type {f[0][:40]!r} is not accepted (options in front of a key aren't either)")
    try:
        blob = base64.b64decode(f[1], validate=True)
    except (binascii.Error, ValueError):
        raise SSHKeyError("the key's base64 part doesn't decode") from None
    if len(blob) > 16 * 1024:
        raise SSHKeyError("the key's base64 part doesn't decode")
    try:
        inner, at = _read_string(blob, 0)
    except ValueError:
        raise SSHKeyError("the key's base64 part doesn't decode") from None
    if inner != f[0].encode():
        raise SSHKeyError(f"the key says it is {inner[:40].decode(errors='replace')!r} inside, not {f[0]!r}")
    bits = None
    if f[0] == "ssh-rsa":
        try:
            _, at = _read_string(blob, at)
            n, _ = _read_string(blob, at)
        except ValueError:
            raise SSHKeyError("a broken RSA key") from None
        bits = int.from_bytes(n, "big").bit_length()
        if bits < MIN_RSA_BITS:
            raise SSHKeyError(f"RSA keys need {MIN_RSA_BITS} bits or more, this one has {bits}")
    comment = " ".join(f[2:])
    if len(comment) > MAX_COMMENT or not COMMENT_RE.match(comment):
        raise SSHKeyError(f"the comment may have up to {MAX_COMMENT} letters, digits, spaces and @._+:=/()-")
    sha = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return {"type": f[0], "comment": comment, "sha256": sha, "bits": bits,
            "line": f"{f[0]} {f[1]}" + (f" {comment}" if comment else "")}


def _check_user(user) -> str:
    user = str(user or "").strip()
    if not USER_RE.match(user):
        raise SSHKeyError(f"{user[:40]!r} is not a user name (lower case letters, digits, _ . -)")
    return user


# ----------------------------------------------------------------------------- keys and grants
def key_to_dict(k: SSHKey, grants: list[SSHKeyGrant] | None = None) -> dict:
    p = parse(k.public_key)
    d = {"id": k.id, "name": k.name, "owner": k.owner, "type": p["type"], "comment": p["comment"], "bits": p["bits"],
         "sha256": k.sha256, "public_key": k.public_key, "note": k.note, "created_by": k.created_by,
         "created_at": agents._iso(k.created_at)}
    if grants is not None:
        d["grants"] = [{"id": g.id, "host": g.host, "user": g.user} for g in grants if g.key_id == k.id]
    return d


def list_keys() -> list[dict]:
    keys = list(db.session.scalars(db.select(SSHKey).order_by(SSHKey.name)))
    grants = list(db.session.scalars(db.select(SSHKeyGrant).order_by(SSHKeyGrant.host, SSHKeyGrant.user)))
    return [key_to_dict(k, grants) for k in keys]


def add_key(data: dict, by: str) -> SSHKey:
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise SSHKeyError("give the key a name of up to 80 characters")
    p = parse(str(data.get("public_key") or ""))
    if db.session.scalar(db.select(SSHKey).where(SSHKey.name == name)):
        raise SSHKeyError(f"there is a key called {name!r} already")
    same = db.session.scalar(db.select(SSHKey).where(SSHKey.sha256 == p["sha256"]))
    if same is not None:
        raise SSHKeyError(f"this key is in labdex already, as {same.name!r}")
    k = SSHKey(name=name, owner=str(data.get("owner") or "")[:128], public_key=p["line"], sha256=p["sha256"],
               note=str(data.get("note") or "")[:2000], created_by=by)
    db.session.add(k)
    db.session.flush()
    audit.record("create", "ssh_key", name, after={"sha256": k.sha256, "owner": k.owner, "type": p["type"]},
                 summary=f"SSH key {name} added ({k.sha256})")
    return k


def update_key(k: SSHKey, data: dict) -> None:
    before = {"name": k.name, "owner": k.owner, "note": k.note}
    if "name" in data:
        name = str(data["name"] or "").strip()
        if not name or len(name) > 80:
            raise SSHKeyError("give the key a name of up to 80 characters")
        if name != k.name and db.session.scalar(db.select(SSHKey).where(SSHKey.name == name)):
            raise SSHKeyError(f"there is a key called {name!r} already")
        k.name = name
    if "owner" in data:
        k.owner = str(data["owner"] or "")[:128]
    if "note" in data:
        k.note = str(data["note"] or "")[:2000]
    audit.record("update", "ssh_key", k.name, before=before, after={"name": k.name, "owner": k.owner, "note": k.note})


def delete_key(k: SSHKey) -> None:
    hosts = sorted({g.host for g in db.session.scalars(db.select(SSHKeyGrant).where(SSHKeyGrant.key_id == k.id))})
    db.session.execute(db.delete(SSHKeyGrant).where(SSHKeyGrant.key_id == k.id))
    db.session.delete(k)
    audit.record("delete", "ssh_key", k.name, before={"sha256": k.sha256, "hosts": hosts},
                 summary=f"SSH key {k.name} removed; apply to {', '.join(hosts) or 'no host'} to take it off")


def grant(k: SSHKey, host: str, user: str, by: str) -> SSHKeyGrant:
    host = str(host or "").strip()
    if host != ALL_HOSTS and store.find_host(host) is None:
        raise SSHKeyError(f"no host {host!r}")
    user = _check_user(user)
    if db.session.scalar(db.select(SSHKeyGrant).where(SSHKeyGrant.key_id == k.id, SSHKeyGrant.host == host, SSHKeyGrant.user == user)):
        raise SSHKeyError(f"{k.name} may log in as {user} on {host} already")
    g = SSHKeyGrant(key_id=k.id, host=host, user=user, created_by=by)
    db.session.add(g)
    db.session.flush()
    where = "every host" if host == ALL_HOSTS else host
    audit.record("grant", "ssh_key", k.name, after={"host": host, "user": user, "sha256": k.sha256},
                 summary=f"SSH key {k.name} may log in as {user} on {where}")
    return g


def revoke_grant(g: SSHKeyGrant) -> None:
    k = db.session.get(SSHKey, g.key_id)
    db.session.delete(g)
    audit.record("ungrant", "ssh_key", k.name if k else str(g.key_id), before={"host": g.host, "user": g.user},
                 summary=f"SSH key {k.name if k else g.key_id} may no longer log in as {g.user} on {g.host}")


# ----------------------------------------------------------------------------- break-glass
def set_break_glass(host: str, user: str, public_key: str, by: str, source: str = "pasted") -> SSHBreakGlass:
    user = _check_user(user)
    p = parse(public_key)
    a = agents.current(host)
    if a is not None and a.ssh_users and user not in a.ssh_users:
        raise SSHKeyError(f"{user} is not in the ssh_users of {host}'s agent.conf ({', '.join(a.ssh_users)})")
    bg = db.session.get(SSHBreakGlass, host)
    before = {"user": bg.user, "sha256": bg.sha256} if bg else None
    if bg is None:
        bg = SSHBreakGlass(host=host)
        db.session.add(bg)
    bg.user, bg.public_key, bg.sha256, bg.source, bg.created_by = user, p["line"], p["sha256"], source, by
    bg.created_at = utcnow()
    audit.record("break-glass", "host", host, before=before, after={"user": user, "sha256": p["sha256"], "source": source},
                 summary=f"break-glass key for {user} on {host} set ({source}, {p['sha256']}); apply to put it on the host")
    return bg


def generate_break_glass(host: str, user: str, by: str) -> tuple[SSHBreakGlass, str]:
    """A new Ed25519 pair. Returns the private key (OpenSSH format) this once; only the public half
    is stored. The caller shows it and asks to keep it offline (a password manager)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    comment = f"break-glass@{host}"[:MAX_COMMENT]
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                serialization.NoEncryption()).decode()
    bg = set_break_glass(host, user, f"{pub} {comment}" if COMMENT_RE.match(comment) else pub, by, source="generated")
    return bg, private


def break_glass_to_dict(bg: SSHBreakGlass | None) -> dict | None:
    if bg is None:
        return None
    return {"host": bg.host, "user": bg.user, "sha256": bg.sha256, "public_key": bg.public_key, "source": bg.source,
            "created_by": bg.created_by, "created_at": agents._iso(bg.created_at)}


# ----------------------------------------------------------------------------- plan and apply
def wanted(host: str, users: list[str]) -> dict[str, list[SSHKey]]:
    """The keys labdex wants for each managed user on the host."""
    rows = db.session.execute(db.select(SSHKeyGrant, SSHKey).join(SSHKey, SSHKey.id == SSHKeyGrant.key_id)
                              .where(SSHKeyGrant.host.in_((host, ALL_HOSTS))).order_by(SSHKey.name))
    out: dict[str, list[SSHKey]] = {u: [] for u in users}
    for g, k in rows:
        if g.user in out and k not in out[g.user]:
            out[g.user].append(k)
    return out


def _grants_for_others(host: str, users: list[str]) -> list[dict]:
    """Grants for this host (not "*") whose user the host doesn't let labdex manage."""
    rows = db.session.execute(db.select(SSHKeyGrant, SSHKey).join(SSHKey, SSHKey.id == SSHKeyGrant.key_id)
                              .where(SSHKeyGrant.host == host))
    return [{"key": k.name, "user": g.user} for g, k in rows if g.user not in users]


def _fps(f: dict | None) -> list[str]:
    return [k.get("sha256") for k in (f or {}).get("keys") or [] if isinstance(k, dict)]


def status(host: str, a: Agent | None = None) -> dict:
    """What labdex wants on the host against the agent's last report: the plan for an apply."""
    a = a if a is not None else agents.current(host)
    bg = db.session.get(SSHBreakGlass, host)
    out = {"host": host, "state": "unknown", "issues": [], "users": [], "unmanaged": [], "can_apply": False,
           "break_glass": break_glass_to_dict(bg), "reported_at": None, "sshd": {}, "ssh_users": []}
    issues = out["issues"]
    if a is None:
        out["state"] = "no_agent"
        issues.append("no agent on this host")
        return out
    if "ssh_keys.report" not in (a.jobs or []):
        out["state"] = "no_agent"
        issues.append("this agent can't report SSH keys: install the Go agent")
        return out
    users = list(a.ssh_users or [])
    out["ssh_users"] = users
    able = "ssh_keys" in (a.capabilities or []) and "ssh_keys" in (a.host_allow or [])
    out["can_apply"] = able and bool(users)
    if not users:
        issues.append("agent.conf names no ssh_users: labdex only reports here")
    elif "ssh_keys" not in (a.host_allow or []):
        issues.append("agent.conf doesn't allow ssh_keys: labdex only reports here")
    elif "ssh_keys" not in (a.capabilities or []):
        issues.append("ssh_keys is off for this agent in labdex (administrators switch it on)")
    for other in _grants_for_others(host, users):
        issues.append(f"{other['key']} is meant for {other['user']}, who isn't in this host's ssh_users")
    report = a.ssh_keys or {}
    if not report.get("users"):
        out["state"] = "unknown"
        issues.append("no report from the agent yet")
        return out
    out["reported_at"] = report.get("at")
    out["sshd"] = report.get("sshd") or {}
    if report.get("sshd_error"):
        issues.append(f"sshd -T failed: {report['sshd_error']}")
    if users and not report.get("uses"):
        issues.append("sshd doesn't read labdex's key files: run install.sh --ssh-keys (the sshd drop-in)")
    want = wanted(host, users)
    drift = unknown = False
    by_user = {u.get("user"): u for u in report["users"] if isinstance(u, dict)}
    glass_fp = bg.sha256 if bg else None
    for user in sorted(set(users) | set(by_user)):
        r = by_user.get(user) or {}
        managed = user in users
        want_fps = {k.sha256: k for k in want.get(user, [])}
        have = set(_fps(r.get("labdex")))
        own = [dict(k, file=f.get("path")) for f in r.get("own") or [] if isinstance(f, dict) for k in f.get("keys") or []]
        row = {"user": user, "managed": managed, "missing_user": bool(r.get("missing")),
               "add": [{"name": k.name, "sha256": fp} for fp, k in want_fps.items() if fp not in have] if managed else [],
               "remove": sorted(have - set(want_fps)) if managed else [],
               "keys": [{"name": k.name, "sha256": fp, "on_host": fp in have} for fp, k in want_fps.items()],
               "own": [{"sha256": k.get("sha256"), "type": k.get("type"), "comment": k.get("comment", ""),
                        "options": bool(k.get("options")), "file": k.get("file"),
                        "known": _known(k.get("sha256"))} for k in own],
               "errors": [f.get("error") for f in r.get("own") or [] if isinstance(f, dict) and f.get("error")],
               "break_glass": _fps(r.get("break_glass"))}
        if r.get("missing"):
            issues.append(f"{user} is in ssh_users but has no account on the host")
        if row["add"] or row["remove"]:
            drift = True
        if row["own"] and not report.get("exclusive"):
            unknown = True
        out["users"].append(row)
    out["unmanaged"] = [{"user": u["user"], "sha256": k["sha256"], "comment": k["comment"], "known": k["known"]}
                        for u in out["users"] for k in u["own"]] if not report.get("exclusive") else []
    reported_glass = [(u["user"], fp) for u in out["users"] for fp in u["break_glass"]]
    if bg is None:
        glass_state = "missing"
        issues.append("no break-glass key for this host in labdex")
    elif (bg.user, glass_fp) not in reported_glass:
        glass_state = "not_on_host"
        drift = True
        issues.append(f"the break-glass key isn't on the host yet (for {bg.user})")
    else:
        glass_state = "ok"
    out["break_glass_state"] = glass_state
    if drift:
        out["state"] = "drift"
    elif glass_state != "ok":
        out["state"] = "no_break_glass"
    elif unknown:
        out["state"] = "unmanaged_keys"
    elif not users:
        out["state"] = "report_only"
    else:
        out["state"] = "in_sync"
    return out


def _known(fp) -> str | None:
    k = db.session.scalar(db.select(SSHKey).where(SSHKey.sha256 == fp)) if fp else None
    return k.name if k else None


def overview() -> list[dict]:
    """Every host with an agent: its SSH key state, for the SSH keys page."""
    out = []
    for a in db.session.scalars(db.select(Agent).where(Agent.revoked_at.is_(None)).order_by(Agent.host)):
        s = status(a.host, a)
        out.append({k: s[k] for k in ("host", "state", "issues", "can_apply", "ssh_users", "reported_at",
                                      "break_glass_state")} |
                   {"unmanaged": len(s["unmanaged"]), "linked": agents.linked(a),
                    "add": sum(len(u["add"]) for u in s["users"]), "remove": sum(len(u["remove"]) for u in s["users"])})
    return out


def params_for(host: str, a: Agent) -> dict:
    """ssh_keys.apply's params: every managed user's whole key list, and the break-glass key."""
    users = list(a.ssh_users or [])
    want = wanted(host, users)
    for u, keys in want.items():
        if len(keys) > MAX_KEYS_PER_USER:
            raise SSHKeyError(f"{u} on {host}: at most {MAX_KEYS_PER_USER} keys")
    params: dict = {"users": {u: [k.public_key for k in keys] for u, keys in want.items()}}
    bg = db.session.get(SSHBreakGlass, host)
    if bg is not None:
        if bg.user not in users:
            raise SSHKeyError(f"the break-glass user {bg.user} is not in {host}'s ssh_users ({', '.join(users)}): set it again")
        params["break_glass"] = {"user": bg.user, "key": bg.public_key}
    return params


def apply(host: str, by: str):
    """Queue ssh_keys.apply for the host. Raises SSHKeyError or agents.AgentError."""
    a = agents.current(host)
    if a is None:
        raise SSHKeyError(f"no agent on {host}")
    if not a.ssh_users:
        raise SSHKeyError(f"{host}'s agent.conf names no ssh_users")
    return agents.queue(a, "ssh_keys.apply", params_for(host, a), by=by, trigger="ssh-keys")


def check_params(params: dict) -> None:
    """The hub's own check of ssh_keys.apply's params (the agent checks again)."""
    if not isinstance(params, dict) or set(params) - {"users", "break_glass"} or not isinstance(params.get("users"), dict):
        raise agents.AgentError("ssh_keys.apply takes users ({user: [keys]}) and break_glass ({user, key})")
    for u, keys in params["users"].items():
        _check_user(u)
        if not isinstance(keys, list) or len(keys) > MAX_KEYS_PER_USER:
            raise agents.AgentError(f"{u}: a list of up to {MAX_KEYS_PER_USER} keys")
        for k in keys:
            parse(str(k))
    bg = params.get("break_glass")
    if bg is not None:
        if not isinstance(bg, dict) or set(bg) != {"user", "key"}:
            raise agents.AgentError("break_glass is {user, key}")
        _check_user(bg["user"])
        parse(str(bg["key"]))
