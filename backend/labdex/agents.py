"""The hub side of the host agents (labdex/agent runs on the hosts): enrolment, keys, revocation,
the job queue with signed jobs, results, metrics and scheduled discovery.

- **Enrolment:** someone who may edit a host makes a one-time token (an hour, shown once). The
  agent swaps it for its own key (``lxa_<id>_<secret>``); only a SHA-256 of the secret is kept.
  A new enrolment for a host revokes the host's older agent.
- **The link:** the agent dials out to ``/api/agents/link`` (api/agents_api.py) with its key. A
  revoked key is refused, and a live link is closed within a second of the revoke. An agent running
  as root links only where the host allows root (``allow_root``), set by an administrator.
- **Jobs:** one of agent.protocol.JOB_TYPES, never a command. Each is signed with the hub's
  Ed25519 key (``<data>/agent-signing.key``); the agent pinned the public half at enrolment. A job
  that changes the host needs its capability switched on here (administrators) and in the host's
  own agent.conf.
- **Audit:** enrolment, revocation, capability changes, every job and its outcome are in the
  change history (object type ``agent``); the agent keeps its own audit.log too.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path

from flask import current_app

from . import agent_pki, audit, store
from .agent import protocol
from .models import Agent, AgentJob, AgentSample, AgentToken, DiscoveredService, Host, Service, db, utcnow

TOKEN_PREFIX = "lxe_"
TOKEN_TTL = timedelta(hours=1)
KEY_RE = re.compile(r"^lxa_([0-9a-f]{8})_([A-Za-z0-9_-]{32,64})$")
LINKED_FOR = timedelta(seconds=protocol.HEARTBEAT * 3)  # no heartbeat this long: not linked any more
LOST_GRACE = timedelta(minutes=5)
SAMPLE_EVERY = timedelta(minutes=5)  # metrics history kept in labdex: one sample per host this often…
SAMPLE_KEEP = timedelta(hours=48)  # …for this long (VictoriaMetrics for longer)
SOURCE = "agent"
# Job types only the Go agent runs. The shared list (agent.protocol.JOB_TYPES) belongs to the
# Python agent too, which stays as it is, so these live here and go only to an agent whose hello
# lists them.
HUB_ONLY_JOBS = {
    "ansible.session": {"write": "ansible", "timeout": 3600,
                        "about": "let labdex reach this host's sshd through the link for one Ansible run"},
}
JOB_TYPES = {**protocol.JOB_TYPES, **HUB_ONLY_JOBS}
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_SESSION_MINUTES = 60


def capabilities_for(job_type: str, params: dict) -> set[str]:
    spec = HUB_ONLY_JOBS.get(job_type)
    return {spec["write"]} if spec else protocol.capabilities_for(job_type, params)


class AgentError(ValueError):
    """Something the caller asked that can't be done; the text says why (shown as is)."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# ----------------------------------------------------------------------------- the signing key
def _key_path() -> Path:
    return Path(current_app.config["LABDEX_DATA_DIR"]) / "agent-signing.key"


def signing_key():
    """The hub's Ed25519 key for signing jobs, made once in the data folder (mode 0600)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    cached = current_app.extensions["labdex"].get("agent_signing_key")
    if cached is not None:
        return cached
    path = _key_path()
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    else:
        key = Ed25519PrivateKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process made it first: use that one
            return signing_key()
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
    current_app.extensions["labdex"]["agent_signing_key"] = key
    return key


def public_key() -> str:
    from cryptography.hazmat.primitives import serialization
    raw = signing_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return protocol.b64(raw)


# ----------------------------------------------------------------------------- enrolment
def make_token(host: Host, by: str, allow_root: bool = False, replaces: str = "") -> tuple[AgentToken, str]:
    """A one-time enrolment token for the host. The token itself is returned this once. With
    `replaces` (the host's agent id) it rerolls that agent's key: enrolling with it revokes the old one."""
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    t = AgentToken(host=host.name, token_hash=_hash(secret), created_by=by, expires_at=utcnow() + TOKEN_TTL, allow_root=allow_root)
    db.session.add(t)
    what = f"new key for agent {replaces} on {host.name}" if replaces else f"enrolment token for {host.name}"
    audit.record("reroll" if replaces else "enrol-token", "agent", host.name,
                 summary=f"{what}, valid an hour" + (", root allowed" if allow_root else ""))
    db.session.flush()
    return t, secret


def enrol(token: str, info: dict, address: str = "") -> tuple[Agent, str]:
    """Swap a token for an agent and its key. Raises AgentError (the same text for every bad token)."""
    now = utcnow()
    t = db.session.scalar(db.select(AgentToken).where(AgentToken.token_hash == _hash(str(token or ""))))
    if t is None or t.used_at is not None or t.expires_at < now:
        raise AgentError("unknown, used or expired enrolment token")
    if store.find_host(t.host) is None:
        raise AgentError("the host of this token is gone")
    t.used_at = now
    actor = (f"agent:{t.host}", "agent")
    for old in db.session.scalars(db.select(Agent).where(Agent.host == t.host, Agent.revoked_at.is_(None))):
        _revoke(old, "re-enrolled", actor)
    agent_id = secrets.token_hex(4)
    while db.session.get(Agent, agent_id) is not None:
        agent_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    a = Agent(id=agent_id, host=t.host, key_hash=_hash(secret), enrolled_by=t.created_by, allow_root=t.allow_root,
              version=str(info.get("version", ""))[:32], hostname=str(info.get("hostname", ""))[:253], address=address[:64],
              root=bool(info.get("root")), host_allow=_caps(info.get("allow")))
    cert = ""
    if agent_pki.mode() != "off" and info.get("csr"):
        try:
            cert, a.cert_sha256, a.cert_not_after = agent_pki.issue(info["csr"], agent_id)
        except agent_pki.CertError as e:
            raise AgentError(str(e)) from None
    elif agent_pki.mode() == "required":
        raise AgentError("this labdex requires a client certificate: use an agent that sends a certificate request")
    db.session.add(a)
    audit.record("enrol", "agent", t.host, after=_audit_view(a), actor=actor,
                 summary=f"agent {agent_id} enrolled on {a.hostname or t.host} (token by {t.created_by})"
                 + (", client certificate issued" if cert else ""))
    db.session.flush()
    return a, f"lxa_{agent_id}_{secret}", cert


def renew_cert(a: Agent, csr: str) -> str:
    """A new client certificate for a linked agent. The old one keeps working until the agent links
    with the new one, so a renewal the agent couldn't save doesn't lock it out."""
    try:
        cert, sha, not_after = agent_pki.issue(csr, a.id)
    except agent_pki.CertError as e:
        raise AgentError(str(e)) from None
    if a.cert_sha256:
        a.cert_prev_sha256 = a.cert_sha256
    a.cert_sha256, a.cert_not_after = sha, not_after
    audit.record("cert", "agent", a.host, actor=(f"agent:{a.host}", "agent"),
                 summary=f"client certificate for agent {a.id} renewed, valid until {not_after:%Y-%m-%d}")
    return cert


def cert_seen(a: Agent, der: bytes | None) -> None:
    """The link presented a certificate that checked out (or none, in optional mode)."""
    a.link_cert = der is not None
    if der is not None and hashlib.sha256(der).hexdigest() == a.cert_sha256:
        a.cert_prev_sha256 = ""  # the renewal arrived: the old certificate is done


def _caps(value) -> list[str]:
    return sorted({str(c) for c in value or [] if str(c) in protocol.CAPABILITIES}) if isinstance(value, list) else []


def authenticate(key: str) -> Agent | None:
    m = KEY_RE.match(key or "")
    if not m:
        return None
    a = db.session.get(Agent, m.group(1))
    if a is None or a.revoked_at is not None or not hmac.compare_digest(a.key_hash, _hash(m.group(2))):
        return None
    return a


def _revoke(a: Agent, why: str, actor=None) -> None:
    a.revoked_at = utcnow()
    a.revoked_by = (actor or audit._actor())[0]
    for j in db.session.scalars(db.select(AgentJob).where(AgentJob.agent_id == a.id, AgentJob.status.in_(("queued", "sent", "running")))):
        j.status, j.error, j.finished_at = "cancelled", f"agent revoked ({why})", utcnow()
    audit.record("revoke", "agent", a.host, before=_audit_view(a), actor=actor, summary=f"agent {a.id} revoked: {why}")


def revoke(a: Agent, why: str = "revoked") -> None:
    if a.revoked_at is None:
        _revoke(a, why)


def current(host: str) -> Agent | None:
    """The host's agent that isn't revoked."""
    return db.session.scalar(db.select(Agent).where(Agent.host == host, Agent.revoked_at.is_(None)).order_by(Agent.enrolled_at.desc()))


def linked(a: Agent, now=None) -> bool:
    now = now or utcnow()
    return bool(a.connected) and a.revoked_at is None and a.last_seen is not None and now - a.last_seen < LINKED_FOR


def _audit_view(a: Agent) -> dict:
    return {"id": a.id, "host": a.host, "capabilities": list(a.capabilities or []), "allow_root": a.allow_root,
            "ansible_become": a.ansible_become,
            "discovery_every": a.discovery_every, "patches_every": a.patches_every, "revoked": a.revoked_at is not None}


def update_settings(a: Agent, data: dict) -> None:
    """Capabilities and root are for administrators (the API checks); the schedules for host editors."""
    before = _audit_view(a)
    errors = []
    if "capabilities" in data:
        caps = data["capabilities"]
        if not isinstance(caps, list) or any(c not in protocol.CAPABILITIES for c in caps):
            errors.append({"field": "capabilities", "message": f"a list of {', '.join(protocol.CAPABILITIES)}"})
        else:
            a.capabilities = sorted(set(caps))
    if "allow_root" in data:
        a.allow_root = bool(data["allow_root"])
    if "ansible_become" in data:
        a.ansible_become = bool(data["ansible_become"])
    for field in ("discovery_every", "patches_every"):
        if field in data:
            v = data[field]
            if not isinstance(v, int) or isinstance(v, bool) or v < 0 or (0 < v < 5) or v > 7 * 24 * 60:
                errors.append({"field": field, "message": "minutes: 0 (off) or 5 to 10080"})
            else:
                setattr(a, field, v)
    if errors:
        raise store.ValidationError(errors)
    audit.record("update", "agent", a.host, before=before, after=_audit_view(a))


# ----------------------------------------------------------------------------- jobs
def queue(a: Agent, job_type: str, params: dict | None = None, by: str = "system", trigger: str = "manual") -> AgentJob:
    params = dict(params or {})
    if a.revoked_at is not None:
        raise AgentError("this agent is revoked")
    if job_type not in JOB_TYPES:
        raise AgentError(f"unknown job type {job_type!r}; there are: {', '.join(JOB_TYPES)}")
    if job_type in HUB_ONLY_JOBS and job_type not in (a.jobs or []):
        raise AgentError(f"this agent doesn't run {job_type}; the Go agent does")
    need = capabilities_for(job_type, params)
    off_here = need - set(a.capabilities or [])
    if off_here:
        raise AgentError(f"switch on {', '.join(sorted(off_here))} for this agent first (administrators)")
    off_there = need - set(a.host_allow or [])
    if off_there:
        raise AgentError(f"the host's agent.conf doesn't allow {', '.join(sorted(off_there))}")
    if job_type == "ansible.run":
        params = _ansible_params(a, params)
    elif job_type == "ansible.session":
        m = params.get("minutes")
        if set(params) - {"session", "minutes"} or not SESSION_RE.match(str(params.get("session", ""))) or \
                not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= MAX_SESSION_MINUTES:
            raise AgentError(f"ansible.session takes session (8 to 64 letters, digits, - or _) and minutes (1 to {MAX_SESSION_MINUTES})")
    elif job_type == "patch.apply":
        if set(params) - {"reboot"}:
            raise AgentError("patch.apply takes only reboot")
    elif params and job_type != "ping":
        raise AgentError(f"{job_type} takes no parameters")
    now = utcnow()
    j = AgentJob(id=str(uuid.uuid4()), agent_id=a.id, host=a.host, type=job_type, params=params, trigger=trigger,
                 created_by=by, created_at=now, expires_at=now + timedelta(seconds=protocol.JOB_TTL))
    db.session.add(j)
    if trigger != "schedule":  # scheduled read-only runs would flood the history
        audit.record("job", "agent", a.host, after={"id": j.id, "type": job_type, "params": params},
                     summary=f"{job_type} queued for {a.host}")
    db.session.flush()
    _wake(a.id)
    return j


def _wake(agent_id: str) -> None:
    """Tell this process's link for the agent (if it has one) that a job is waiting."""
    ev = current_app.extensions["labdex"].setdefault("agent_wake", {}).get(agent_id)
    if ev is not None:
        ev.set()


def signed(j: AgentJob) -> dict:
    env = {"v": 1, "id": j.id, "agent": j.agent_id, "host": j.host, "type": j.type, "params": j.params or {},
           "issued": int(j.created_at.timestamp()), "expires": int(j.expires_at.timestamp()), "by": j.created_by}
    return protocol.sign(signing_key(), env)


def to_send(a: Agent) -> list[AgentJob]:
    """Queued jobs for the agent, oldest first; ones that waited too long expire here."""
    now = utcnow()
    out = []
    for j in db.session.scalars(db.select(AgentJob).where(AgentJob.agent_id == a.id, AgentJob.status == "queued").order_by(AgentJob.created_at)):
        if j.expires_at < now:
            j.status, j.error, j.finished_at = "expired", "the agent wasn't linked in time", now
        else:
            out.append(j)
    return out


def acked(a: Agent, job_id: str) -> None:
    j = db.session.get(AgentJob, str(job_id))
    if j is not None and j.agent_id == a.id and j.status in ("queued", "sent"):
        j.status, j.started_at = "running", utcnow()


def finished(a: Agent, msg: dict) -> AgentJob | None:
    """A result from the agent (live or from its buffer). A second copy of a result is ignored."""
    j = db.session.get(AgentJob, str(msg.get("id", "")))
    if j is None or j.agent_id != a.id or j.status in ("succeeded", "failed", "refused", "cancelled"):
        return None
    ok = bool(msg.get("ok"))
    result = msg.get("result") if isinstance(msg.get("result"), dict) else None
    j.status = "succeeded" if ok else ("refused" if msg.get("refused") else "failed")
    j.error = str(msg.get("error") or "")[:2000]
    j.result = result
    j.finished_at = utcnow()
    if ok and result is not None:
        try:
            _use_result(a, j, result)
        except Exception as e:  # a bad result must not lose the job's outcome
            current_app.logger.exception("agent %s: using the %s result failed", a.id, j.type)
            j.error = f"stored, but using the result failed: {e}"[:2000]
    if j.trigger != "schedule" or not ok:
        audit.record("job-result", "agent", a.host, after={"id": j.id, "type": j.type, "status": j.status, "error": j.error[:300]},
                     actor=(f"agent:{a.host}", "agent"), summary=f"{j.type} on {a.host}: {j.status}" + (f" ({j.error[:120]})" if j.error else ""))
    return j


def _use_result(a: Agent, j: AgentJob, result: dict) -> None:
    if j.type == "facts":
        a.facts = result
    elif j.type == "discovery.services":
        record_discovery(a, result)
    elif j.type == "patch.report":
        _report_patches(a, result)
    elif j.type == "patch.apply" and isinstance(result.get("report"), dict):
        _report_patches(a, result["report"])


def _report_patches(a: Agent, report: dict) -> None:
    from . import patches
    keep = {k: report[k] for k in ("pending", "security", "reboot", "packages", "os", "kernel", "patched_at", "error") if k in report}
    patches.report(SOURCE, {a.host: keep})


def hello(a: Agent, msg: dict, worker: str, address: str = "") -> None:
    a.version = str(msg.get("version", ""))[:32]
    a.hostname = str(msg.get("hostname", ""))[:253]
    a.root = bool(msg.get("root"))
    a.host_allow = _caps(msg.get("allow"))
    a.jobs = sorted({str(t) for t in msg.get("jobs") or [] if str(t) in JOB_TYPES})
    if isinstance(msg.get("facts"), dict):
        a.facts = msg["facts"]
    a.address = address[:64]
    a.connected, a.linked_at, a.last_seen = worker, utcnow(), utcnow()


def heartbeat(a: Agent, msg: dict) -> None:
    a.last_seen = utcnow()
    if isinstance(msg.get("metrics"), dict) and not msg.get("buffered"):
        clean = {k: v for k, v in msg["metrics"].items() if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}
        a.metrics = clean | {"at": utcnow().isoformat(timespec="seconds") + "Z"}
        _sample(a, clean)


def _pct(used, total):
    return round(100 * used / total, 1) if isinstance(used, (int, float)) and total else None


def _sample(a: Agent, m: dict, now=None) -> None:
    """Keep a sample for the charts every SAMPLE_EVERY (heartbeats come every few seconds)."""
    now = now or utcnow()
    last = current_app.extensions["labdex"].setdefault("agent_sampled", {})
    if a.host in last and now - last[a.host] < SAMPLE_EVERY:
        return
    if a.host not in last:  # after a restart: the database knows when the last one was
        at = db.session.scalar(db.select(db.func.max(AgentSample.at)).where(AgentSample.host == a.host))
        if at is not None and now - at < SAMPLE_EVERY:
            last[a.host] = at
            return
    last[a.host] = now
    mt, ma = m.get("mem_total"), m.get("mem_available")
    db.session.add(AgentSample(
        host=a.host, at=now, cpu=m.get("cpu_percent"), load1=m.get("load1"),
        mem=_pct(mt - ma, mt) if isinstance(mt, (int, float)) and isinstance(ma, (int, float)) else None,
        disk=_pct(m.get("root_used"), m.get("root_total")),
        net_rx=int(m["net_rx_bytes"]) if isinstance(m.get("net_rx_bytes"), (int, float)) else None,
        net_tx=int(m["net_tx_bytes"]) if isinstance(m.get("net_tx_bytes"), (int, float)) else None))


def history(host: str, now=None) -> list[dict]:
    """The host's samples of the last 48 hours, oldest first; network as bytes per second."""
    now = now or utcnow()
    rows = list(db.session.scalars(db.select(AgentSample).where(AgentSample.host == host, AgentSample.at >= now - SAMPLE_KEEP)
                                   .order_by(AgentSample.at)))
    out, prev = [], None
    for r in rows:
        rate = {}
        if prev is not None:
            dt = (r.at - prev.at).total_seconds()
            for k in ("net_rx", "net_tx"):
                a, b = getattr(prev, k), getattr(r, k)
                # a reboot resets the counters: no rate for that step
                rate[k] = round((b - a) / dt) if dt > 0 and a is not None and b is not None and b >= a else None
        out.append({"at": _iso(r.at), "cpu": r.cpu, "mem": r.mem, "disk": r.disk, "load1": r.load1,
                    "rx": rate.get("net_rx"), "tx": rate.get("net_tx")})
        prev = r
    return out


def unlinked(a: Agent, worker: str) -> None:
    if a.connected == worker:
        a.connected, a.link_cert = "", False


def tick(now=None) -> dict:
    """Every half minute (the job scheduler): expire and lose jobs, queue scheduled ones."""
    now = now or utcnow()
    out = {"expired": 0, "lost": 0, "queued": 0}
    for j in db.session.scalars(db.select(AgentJob).where(AgentJob.status == "queued", AgentJob.expires_at < now)):
        j.status, j.error, j.finished_at = "expired", "the agent wasn't linked in time", now
        out["expired"] += 1
    for j in db.session.scalars(db.select(AgentJob).where(AgentJob.status.in_(("sent", "running")))):
        limit = timedelta(seconds=JOB_TYPES.get(j.type, {}).get("timeout", 600)) + LOST_GRACE
        if (j.sent_at or j.created_at) + limit < now:
            j.status, j.error, j.finished_at = "failed", "no result from the agent in time", now
            out["lost"] += 1
    db.session.execute(db.delete(AgentSample).where(AgentSample.at < now - SAMPLE_KEEP))
    for a in db.session.scalars(db.select(Agent).where(Agent.revoked_at.is_(None), Agent.connected != "")):
        if not linked(a, now):
            continue
        for job_type, every in (("discovery.services", a.discovery_every), ("patch.report", a.patches_every)):
            if every and _due(a, job_type, every, now):
                queue(a, job_type, by="schedule", trigger="schedule")
                out["queued"] += 1
    db.session.flush()
    return out


def _due(a: Agent, job_type: str, minutes: int, now) -> bool:
    last = db.session.scalar(db.select(AgentJob).where(AgentJob.agent_id == a.id, AgentJob.type == job_type)
                             .order_by(AgentJob.created_at.desc()).limit(1))
    if last is None:
        return True
    if last.status in ("queued", "sent", "running"):
        return False
    return last.created_at + timedelta(minutes=minutes) <= now


# ----------------------------------------------------------------------------- discovery
def _host_addresses(name: str) -> list[str]:
    h = store.find_host(name)
    if h is None:
        return []
    out = []
    for iface in h.interfaces:
        for ip in iface.all_addresses:
            if ip and ip not in out:
                out.append(ip)
    return out


def _wildcard(addr: str) -> bool:
    return addr in ("0.0.0.0", "::", "*")


def _loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def record_discovery(a: Agent, result: dict) -> dict:
    """Keep the result for the host page, and put the host's reachable listeners into Discovery
    (source "agent"): one row per inventory address the port listens on. Loopback-only ports stay
    on the host page; ports the agent no longer sees close."""
    a.discovery = result
    addrs = _host_addresses(a.host)
    now = utcnow()
    procs = {p.get("pid"): p for p in result.get("processes") or [] if isinstance(p, dict)}
    seen = set()
    for s in result.get("listeners") or []:
        if not isinstance(s, dict) or not isinstance(s.get("port"), int):
            continue
        addr, proto = str(s.get("address", "")), str(s.get("proto", "tcp"))
        if _loopback(addr):
            continue
        targets = addrs if _wildcard(addr) else [x for x in addrs if x == addr]
        pids = [p.get("pid") for p in s.get("pids") or [] if isinstance(p, dict)]
        first = procs.get(pids[0]) if pids else None
        product = (s.get("container") or {}).get("image") or (first or {}).get("name") or ((s.get("pids") or [{}])[0].get("name", "") if s.get("pids") else "")
        unit = ", ".join(s.get("units") or []) or (s.get("container") or {}).get("name", "")
        for ip in targets:
            key = (ip, s["port"], proto)
            seen.add(key)
            ds = db.session.scalar(db.select(DiscoveredService).where(
                DiscoveredService.ip == ip, DiscoveredService.port == s["port"], DiscoveredService.proto == proto))
            if ds is None:
                ds = DiscoveredService(ip=ip, port=s["port"], proto=proto, first_seen=now)
                db.session.add(ds)
            ds.state, ds.last_seen, ds.source = "open", now, SOURCE
            ds.service = (unit or ds.service or "")[:64]
            ds.product = (str(product) or ds.product or "")[:128]
    closed = 0
    if addrs:
        for ds in db.session.scalars(db.select(DiscoveredService).where(DiscoveredService.ip.in_(addrs), DiscoveredService.source == SOURCE)):
            if (ds.ip, ds.port, ds.proto) not in seen and ds.state == "open":
                ds.state, ds.last_seen = "closed", now
                closed += 1
    db.session.flush()
    return {"open": len(seen), "closed": closed}


# ----------------------------------------------------------------------------- adopting what it found
def _svc_name(raw: str) -> str:
    raw = re.sub(r"\.(service|socket)$", "", str(raw or "").strip().lower())
    return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")[:56]


def _listener_name(s: dict, procs: dict) -> str:
    """A name for a discovered port: its container, unit or process, else port-<n>."""
    if s.get("container"):
        return _svc_name(s["container"].get("name", ""))
    for u in s.get("units") or []:
        if u and u != "init.scope":
            return _svc_name(u)
    for p in s.get("pids") or []:
        name = (procs.get(p.get("pid")) or {}).get("name") or p.get("name")
        if name:
            return _svc_name(name)
    return ""


def declared(host: Host, disc: dict | None) -> dict:
    """Which discovered ports and units the host already declares: {"ports": {"22/tcp": name}, "units": {unit: name}}."""
    by_port = {f"{s.port}/{s.proto or 'tcp'}": s.name for s in host.services if s.port}
    names = {s.name for s in host.services}
    units = {}
    for u in (disc or {}).get("units") or []:
        if isinstance(u, dict) and _svc_name(u.get("unit", "")) in names:
            units[u["unit"]] = _svc_name(u["unit"])
    return {"ports": by_port, "units": units}


def adopt(host: Host, a: Agent, data: dict) -> dict:
    """Declare a port or a systemd unit the agent found as a service of its host (in the site's
    inventory, like any edit). Only what the last discovery reported can be adopted."""
    disc = a.discovery or {}
    procs = {p.get("pid"): p for p in disc.get("processes") or [] if isinstance(p, dict)}
    before = store.host_to_dict(host)
    want = _svc_name(data.get("name", ""))
    if data.get("unit"):
        unit = next((u for u in disc.get("units") or [] if isinstance(u, dict) and u.get("unit") == data["unit"]), None)
        if unit is None:
            raise AgentError(f"the agent didn't report {data['unit']}; run Discover services first")
        name = want or _svc_name(unit["unit"])
        if any(s.name == name for s in host.services):
            raise AgentError(f"{host.name} already has a service {name}")
        svc = Service(name=name, port=None, proto="tcp", description=str(unit.get("description") or unit["unit"])[:200])
    else:
        port, proto = data.get("port"), str(data.get("proto") or "tcp")
        found = [s for s in disc.get("listeners") or [] if isinstance(s, dict) and s.get("port") == port and s.get("proto", "tcp") == proto]
        if not found or not isinstance(port, int):
            raise AgentError(f"the agent didn't report {port}/{proto}; run Discover services first")
        if any(s.port == port and (s.proto or "tcp") == proto for s in host.services):
            raise AgentError(f"{host.name} already declares {port}/{proto}")
        name = want or _listener_name(found[0], procs) or f"port-{port}"
        if any(s.name == name for s in host.services):
            name = f"{name}-{port}"[:63]
        scheme = "https" if port in (443, 8443) else "ssh" if port == 22 else ""
        # bound only to loopback: reachable on the host alone, nothing for DNS or the firewall
        local = all(_loopback(str(s.get("address", ""))) for s in found)
        svc = Service(name=name, port=port, proto=proto, scheme=scheme, description="listens on loopback only" if local else "")
    host.services.append(svc)
    db.session.flush()
    audit.record("update", "host", host.name, before, store.host_to_dict(host),
                 summary=f"from its agent: service {svc.name}" + (f" ({svc.port}/{svc.proto})" if svc.port else ""))
    return {"name": svc.name, "port": svc.port, "proto": svc.proto}


# ----------------------------------------------------------------------------- ansible bundles
def _bundle_dir() -> Path:
    return Path(current_app.config["LABDEX_DATA_DIR"]) / "agent-bundles"


def _ansible_params(a: Agent, params: dict) -> dict:
    """{playbook, vars?, check?} → the signed parameters: the bundle's hash, the commit, the playbook.
    The playbook comes from the git working copy of the host's own site."""
    from . import gitsource, sites
    from .agent.ansible import PLAYBOOK
    unknown = set(params) - {"playbook", "vars", "check"}
    if unknown:
        raise AgentError(f"unknown parameters: {', '.join(sorted(unknown))}")
    playbook = str(params.get("playbook", ""))
    if not PLAYBOOK.match(playbook) or ".." in playbook.split("/"):
        raise AgentError("playbook: a .yml path in the site's git repository, such as ansible/site.yml")
    if not isinstance(params.get("vars", {}), dict):
        raise AgentError("vars must be an object")
    h = store.find_host(a.host)
    # a known site code from the database, never a path piece from the request
    want = h.site if h else ""
    code = next((c for c in sites.codes() if c == want), sites.default())
    repo = gitsource.repo_dir(code)
    if not (repo / ".git").exists():
        raise AgentError(f"site {code} has no git working copy: set up its git source first")
    top = playbook.split("/", 1)[0] if "/" in playbook else "."
    try:
        commit = _git(repo, "rev-parse", "HEAD").strip()
        data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar.gz", commit, "--", top],
                              capture_output=True, check=True, timeout=60).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        current_app.logger.exception("agent %s: packing %s failed", a.id, top)
        raise AgentError(f"couldn't pack {top}/ from the site's git working copy (is it in the repository?)") from None
    from .agent.ansible import MAX_BUNDLE
    if len(data) > MAX_BUNDLE:
        raise AgentError(f"the bundle of {top}/ is over {MAX_BUNDLE // 1024 // 1024} MB")
    sha = hashlib.sha256(data).hexdigest()
    d = _bundle_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.tar.gz").write_bytes(data)
    return {"bundle": sha, "playbook": playbook, "commit": commit, "vars": params.get("vars") or {}, "check": bool(params.get("check"))}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=30).stdout


def bundle(a: Agent, sha: str) -> bytes | None:
    """A bundle the agent may fetch: only one named in a job of its own that is running."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return None
    jobs = db.session.scalars(db.select(AgentJob).where(AgentJob.agent_id == a.id, AgentJob.type == "ansible.run",
                                                        AgentJob.status.in_(("sent", "running"))))
    if not any((j.params or {}).get("bundle") == sha for j in jobs):
        return None
    path = _bundle_dir() / f"{sha}.tar.gz"
    return path.read_bytes() if path.exists() else None


def usage(now=None) -> dict[str, dict]:
    """By host: CPU, memory and root disk use in percent from each linked agent's last heartbeat.
    Only the latest values; the time series go to VictoriaMetrics (/api/agents/metrics)."""
    now = now or utcnow()
    out = {}
    for a in db.session.scalars(db.select(Agent).where(Agent.revoked_at.is_(None))):
        m = a.metrics or {}
        if not m or not linked(a, now):
            continue
        mt, ma = m.get("mem_total") or 0, m.get("mem_available")
        rt, ru = m.get("root_total") or 0, m.get("root_used")
        out[a.host] = {"cpu": m.get("cpu_percent"), "cpus": (a.facts or {}).get("cpus") or None,
                       "mem": round(100 * (mt - ma) / mt, 1) if mt and ma is not None else None, "mem_total": mt or None,
                       "disk": round(100 * ru / rt, 1) if rt and ru is not None else None, "disk_total": rt or None,
                       "load1": m.get("load1"), "uptime": m.get("uptime"), "at": m.get("at")}
    return out


# ----------------------------------------------------------------------------- views
def to_dict(a: Agent, now=None) -> dict:
    now = now or utcnow()
    return {"id": a.id, "host": a.host, "linked": linked(a, now), "revoked": a.revoked_at is not None,
            "enrolled_at": _iso(a.enrolled_at), "enrolled_by": a.enrolled_by, "revoked_at": _iso(a.revoked_at), "revoked_by": a.revoked_by,
            "last_seen": _iso(a.last_seen), "linked_at": _iso(a.linked_at), "version": a.version, "hostname": a.hostname,
            "address": a.address, "root": a.root, "allow_root": a.allow_root, "capabilities": list(a.capabilities or []),
            "jobs": list(a.jobs or []), "ansible_become": a.ansible_become,
            "host_allow": list(a.host_allow or []), "discovery_every": a.discovery_every, "patches_every": a.patches_every,
            "facts": a.facts or {}, "metrics": a.metrics or {},
            "cert": {"mode": agent_pki.mode(), "issued": bool(a.cert_sha256), "sha256": a.cert_sha256,
                     "not_after": _iso(a.cert_not_after), "on_link": bool(a.link_cert and linked(a, now))}}


def job_to_dict(j: AgentJob, with_result: bool = False) -> dict:
    d = {"id": j.id, "agent": j.agent_id, "host": j.host, "type": j.type, "params": j.params or {}, "status": j.status,
         "trigger": j.trigger, "created_by": j.created_by, "created_at": _iso(j.created_at), "sent_at": _iso(j.sent_at),
         "started_at": _iso(j.started_at), "finished_at": _iso(j.finished_at), "error": j.error}
    if with_result:
        d["result"] = j.result
    return d


def _iso(d) -> str | None:
    return d.isoformat(timespec="seconds") + "Z" if d else None


METRICS = {
    "cpu_percent": ("gauge", "CPU use in percent over the last heartbeat"),
    "load1": ("gauge", "load average over 1 minute"), "load5": ("gauge", "load average over 5 minutes"),
    "load15": ("gauge", "load average over 15 minutes"),
    "mem_total": ("gauge", "memory in bytes"), "mem_available": ("gauge", "memory available in bytes"),
    "swap_total": ("gauge", "swap in bytes"), "swap_free": ("gauge", "swap free in bytes"),
    "root_total": ("gauge", "size of / in bytes"), "root_used": ("gauge", "bytes used on /"),
    "uptime": ("gauge", "seconds since boot"),
    "net_rx_bytes": ("counter", "bytes received on all interfaces but lo"), "net_tx_bytes": ("counter", "bytes sent on all interfaces but lo"),
}


def prometheus(agents: list[Agent]) -> str:
    """The latest heartbeat of each agent in the Prometheus text format, for VictoriaMetrics to scrape.
    Labdex keeps no history of its own: the time series live in VictoriaMetrics."""
    now = utcnow()
    lines = ["# HELP labdex_agent_up 1 while the agent is linked", "# TYPE labdex_agent_up gauge"]

    def lbl(a):
        return f'host="{a.host}",agent="{a.id}"'
    for a in agents:
        lines.append(f"labdex_agent_up{{{lbl(a)}}} {int(linked(a, now))}")
    for name, (kind, text) in METRICS.items():
        rows = [(a, (a.metrics or {}).get(name)) for a in agents if linked(a, now)]
        rows = [(a, v) for a, v in rows if isinstance(v, (int, float))]
        if not rows:
            continue
        metric = f"labdex_agent_{name}" + ("_total" if kind == "counter" else "")
        lines += [f"# HELP {metric} {text}", f"# TYPE {metric} {kind}"]
        lines += [f"{metric}{{{lbl(a)}}} {v}" for a, v in rows]
    return "\n".join(lines) + "\n"
