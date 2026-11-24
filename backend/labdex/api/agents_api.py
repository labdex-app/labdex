"""Host agents: enrolment tokens, the agent's WebSocket link, jobs, settings, metrics (agents.py)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import socket
import threading
import zipfile
from pathlib import Path

from flask import Response, current_app, g, jsonify, request

from .. import agent_pki, agents, audit, auth, rbac, store
from ..agent import protocol
from ..models import Agent, AgentJob, db, utcnow
from . import PUBLIC, HOOKS, body, bp, err

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
# reached by hosts, not people: no session and no CSRF header (the enrolment token or nothing secret)
for _ep in ("api.agent_enrol", "api.agent_install_script", "api.agent_download", "api.agent_ca"):
    PUBLIC.add(_ep)
    HOOKS.add(_ep)


def _host_or_404(name: str, action: str):
    h = store.find_host(name)
    if h is None or not g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
        return None
    rbac.require("host", action, rbac.host_facts(store.host_access(h)))
    return h


# ----------------------------------------------------------------------------- what hosts download
# Pure-Python libraries the agent needs, taken from this labdex's own install into the zipapp,
# so hosts need nothing from PyPI (only python3 and its cryptography package). All MIT licensed;
# their licence files go along.
BUNDLED = (("simple_websocket", "simple-websocket"), ("wsproto", "wsproto"), ("h11", "h11"))


def _bundled_files():
    import importlib
    import importlib.metadata
    for module, dist in BUNDLED:
        root = Path(importlib.import_module(module).__file__).parent
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".typed") and "__pycache__" not in path.parts:
                yield f"{module}/{path.relative_to(root).as_posix()}", path.read_bytes()
        d = importlib.metadata.distribution(dist)
        for f in d.files or []:
            if "LICEN" in f.name.upper():
                yield f"licenses/{dist}-{d.version}/{f.name}", Path(d.locate_file(f)).read_bytes()


def agent_package() -> tuple[bytes, str]:
    """labdex-agent.pyz: the agent as one zipapp (package labdex_agent, built from labdex/agent,
    with its libraries). Fixed timestamps, so the same code always gives the same SHA-256."""
    cache = current_app.extensions["labdex"].get("agent_pyz")
    if cache:
        return cache
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name: str, data: bytes):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
        add("__main__.py", b"import sys\nfrom labdex_agent.cli import main\nsys.exit(main())\n")
        for path in sorted(AGENT_DIR.glob("*.py")):
            add(f"labdex_agent/{path.name}", path.read_bytes())
        for name, data in _bundled_files():
            add(name, data)
    data = buf.getvalue()
    out = (data, hashlib.sha256(data).hexdigest())
    current_app.extensions["labdex"]["agent_pyz"] = out
    return out


# The Go agent (agent/ in the repository, docs/develop/agent-protocol.md): one static binary per
# architecture, built into the image. Python stays the default until the Go agent passes the same
# install rehearsal (tools/agent_rehearsal.py --agent go); `install.sh --go` picks it.
GO_ARCHES = ("amd64", "arm64")


def _go_dir() -> Path:
    return Path(current_app.config.get("LABDEX_AGENT_BIN_DIR") or AGENT_DIR / "bin")


def go_binaries() -> dict[str, tuple[Path, str]]:
    """{arch: (path, sha256)} of the Go agent's binaries that are there; {} when none were built."""
    cache = current_app.extensions["labdex"].setdefault("agent_go", {})
    out = {}
    for arch in GO_ARCHES:
        path = _go_dir() / f"labdex-agent-linux-{arch}"
        try:
            st = path.stat()
        except OSError:
            continue
        key = (str(path), st.st_mtime_ns, st.st_size)
        if cache.get(arch, (None,))[0] != key:
            cache[arch] = (key, hashlib.sha256(path.read_bytes()).hexdigest())
        out[arch] = (path, cache[arch][1])
    return out


@bp.get("/agents/install.sh")
def agent_install_script():
    """The install script for hosts (read it before you run it)."""
    return Response((AGENT_DIR / "dist" / "install.sh").read_text(), mimetype="text/x-shellscript")


@bp.get("/agents/download/<name>")
def agent_download(name):
    if name == "labdex-agent.pyz":
        data, sha = agent_package()
        resp = Response(data, mimetype="application/zip")
        resp.headers["X-Labdex-SHA256"] = sha
        return resp
    go = {f"labdex-agent-linux-{arch}": v for arch, v in go_binaries().items()}
    if name in go:
        path, sha = go[name]
        resp = Response(path.read_bytes(), mimetype="application/octet-stream")
        resp.headers["X-Labdex-SHA256"] = sha
        return resp
    if name == "labdex-agent.service":
        return Response((AGENT_DIR / "dist" / "labdex-agent.service").read_text(), mimetype="text/plain")
    return err(404, "no such file")


# ----------------------------------------------------------------------------- enrolment
@bp.post("/agents/enrol")
def agent_enrol():
    """{token, hostname, version, root, allow} → {agent_id, key, host, signing_key}: the key is shown this once."""
    data = body()
    if str(data.get("protocol", protocol.PROTOCOL)) != str(protocol.PROTOCOL):
        return err(426, f"this labdex speaks agent protocol {protocol.PROTOCOL}")
    try:
        a, key, cert = agents.enrol(str(data.get("token", "")), data, auth.client_ip())
    except agents.AgentError as e:
        db.session.rollback()
        return err(403, str(e))
    if a.root and not a.allow_root:
        db.session.rollback()
        return err(403, "this agent runs as root, and the token doesn't allow root for this host")
    db.session.commit()
    out = {"agent_id": a.id, "key": key, "host": a.host, "signing_key": agents.public_key(), "protocol": protocol.PROTOCOL}
    if cert:
        out |= {"client_cert": cert, "ca_cert": agent_pki.ca_pem()}
    return jsonify(out)


@bp.get("/agents/ca.pem")
def agent_ca():
    """The CA of the agents' client certificates, for a reverse proxy that checks them."""
    return Response(agent_pki.ca_pem(), mimetype="application/x-pem-file")


@bp.post("/hosts/<name>/agent/token")
def agent_token(name):
    """A one-time enrolment token for the host, and the command to install the agent with it."""
    h = _host_or_404(name, "edit")
    if h is None:
        return err(404, f"no host '{name}'")
    data = request.get_json(silent=True) or {}
    allow_root = bool(data.get("allow_root"))
    if allow_root:
        rbac.require_admin()
    t, secret = agents.make_token(h, g.principal.label, allow_root)
    db.session.commit()
    _, sha = agent_package()
    base = request.host_url.rstrip("/")
    cmd = (f"curl -fsSLo install.sh {base}/api/agents/install.sh && sudo sh install.sh --url {base} "
           f"--token {secret} --sha256 {sha}" + (" --root" if allow_root else ""))
    out = {"token": secret, "expires_at": agents._iso(t.expires_at), "sha256": sha, "command": cmd}
    go = go_binaries()
    if go:
        # one --sha256 per architecture: install.sh takes the binary that matches any of them
        shas = " ".join(f"--sha256 {v[1]}" for v in go.values())
        out["go"] = {"sha256": {arch: v[1] for arch, v in go.items()},
                     "command": (f"curl -fsSLo install.sh {base}/api/agents/install.sh && sudo sh install.sh --go --url {base} "
                                 f"--token {secret} {shas}" + (" --root" if allow_root else ""))}
    return jsonify(out), 201


# ----------------------------------------------------------------------------- the host's agent
@bp.get("/agents")
def list_agents():
    out = []
    for a in db.session.scalars(db.select(Agent).where(Agent.revoked_at.is_(None)).order_by(Agent.host)):
        h = store.find_host(a.host)
        if h is not None and g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
            out.append(agents.to_dict(a))
    return jsonify({"agents": out, "job_types": protocol.JOB_TYPES, "capabilities": list(protocol.CAPABILITIES)})


@bp.get("/hosts/<name>/agent")
def host_agent(name):
    h = _host_or_404(name, "view")
    if h is None:
        return err(404, f"no host '{name}'")
    a = agents.current(h.name)
    jobs = []
    if a is not None:
        jobs = [agents.job_to_dict(j) for j in db.session.scalars(
            db.select(AgentJob).where(AgentJob.agent_id == a.id).order_by(AgentJob.created_at.desc()).limit(20))]
    return jsonify({"agent": agents.to_dict(a) if a else None, "discovery": (a.discovery if a else None) or None,
                    "jobs": jobs, "job_types": protocol.JOB_TYPES, "capabilities": list(protocol.CAPABILITIES)})


@bp.patch("/hosts/<name>/agent")
def update_host_agent(name):
    h = _host_or_404(name, "edit")
    a = agents.current(h.name) if h else None
    if a is None:
        return err(404, f"no agent on '{name}'")
    data = body()
    if "capabilities" in data or "allow_root" in data:
        rbac.require_admin()  # switching on writes or root is an administrator's call
    agents.update_settings(a, data)
    db.session.commit()
    return jsonify(agents.to_dict(a))


@bp.delete("/hosts/<name>/agent")
def revoke_host_agent(name):
    h = _host_or_404(name, "edit")
    a = agents.current(h.name) if h else None
    if a is None:
        return err(404, f"no agent on '{name}'")
    agents.revoke(a)
    db.session.commit()
    return jsonify(agents.to_dict(a))


@bp.post("/hosts/<name>/agent/jobs")
def queue_agent_job(name):
    h = _host_or_404(name, "edit")
    a = agents.current(h.name) if h else None
    if a is None:
        return err(404, f"no agent on '{name}'")
    data = body()
    try:
        j = agents.queue(a, str(data.get("type", "")), data.get("params") or {}, by=g.principal.label)
    except agents.AgentError as e:
        db.session.rollback()
        return err(409, str(e))
    db.session.commit()
    return jsonify(agents.job_to_dict(j)), 201


@bp.get("/agents/jobs/<job_id>")
def agent_job(job_id):
    j = db.session.get(AgentJob, job_id)
    h = store.find_host(j.host) if j else None
    if h is None or not g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
        return err(404, "no such job")
    return jsonify(agents.job_to_dict(j, with_result=True))


@bp.get("/agents/metrics")
def agent_metrics():
    """Prometheus text: each linked agent's latest heartbeat. Scrape it with an API token."""
    rows = []
    for a in db.session.scalars(db.select(Agent).where(Agent.revoked_at.is_(None)).order_by(Agent.host)):
        h = store.find_host(a.host)
        if h is not None and g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
            rows.append(a)
    return Response(agents.prometheus(rows), mimetype="text/plain; version=0.0.4")


# ----------------------------------------------------------------------------- the link
class _Wake:
    """agents.queue() sets it; the link wakes up and sends the job."""

    def __init__(self, inbox: queue.Queue):
        self.inbox = inbox

    def set(self):
        self.inbox.put(None)


def _link(ws) -> None:
    """One agent's link. A reader thread only receives; this thread does every database step,
    so the session stays in one thread."""
    app = current_app._get_current_object()
    key = request.headers.get(protocol.KEY_HEADER, "")
    a = agents.authenticate(key)
    if a is None:
        db.session.rollback()
        ws.close(reason=protocol.CLOSE_REFUSED, message="unknown or revoked agent key")
        return
    if request.headers.get(protocol.PROTOCOL_HEADER, str(protocol.PROTOCOL)) != str(protocol.PROTOCOL):
        ws.close(reason=protocol.CLOSE_PROTOCOL, message=f"this labdex speaks agent protocol {protocol.PROTOCOL}")
        return
    mtls = agent_pki.mode()
    der = None
    if mtls != "off":
        try:
            der = agent_pki.presented()
            problem = agent_pki.check(a, der)
        except ValueError as e:  # CertError, or a PEM that can't be read
            problem = str(e) or "the client certificate can't be read"
        if problem:
            current_app.logger.warning("agent %s on %s: link refused: %s", a.id, a.host, problem)
            db.session.rollback()
            ws.close(reason=protocol.CLOSE_CERT, message=problem)
            return
    agent_id, address = a.id, auth.client_ip()
    worker = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
    inbox: queue.Queue = queue.Queue()
    gone = threading.Event()
    wakes = app.extensions["labdex"].setdefault("agent_wake", {})
    wakes[agent_id] = _Wake(inbox)

    def read():
        try:
            while not gone.is_set():
                raw = ws.receive(timeout=5)
                if raw is not None:
                    inbox.put(raw)
        except Exception:  # closed by either side
            pass
        finally:
            gone.set()
            inbox.put(None)

    threading.Thread(target=read, name=f"agent-link-{agent_id}", daemon=True).start()

    def send(message: dict) -> None:
        ws.send(json.dumps(message, separators=(",", ":"), default=str))

    said_hello = False
    try:
        while not gone.is_set():
            try:
                raw = inbox.get(timeout=1)
            except queue.Empty:
                raw = None
            a = db.session.get(Agent, agent_id)
            db.session.refresh(a)
            if a.revoked_at is not None:
                ws.close(reason=protocol.CLOSE_REFUSED, message="the agent was revoked")
                break
            if raw is not None:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    msg = None
                if isinstance(msg, dict):
                    t = msg.get("t")
                    if t == "hello":
                        if msg.get("root") and not a.allow_root:
                            audit.record("refuse", "agent", a.host, actor=(f"agent:{a.host}", "agent"),
                                         summary=f"agent {a.id} runs as root; root isn't allowed for {a.host}")
                            db.session.commit()
                            ws.close(reason=protocol.CLOSE_ROOT, message="root isn't allowed for this host")
                            break
                        agents.hello(a, msg, worker, address)
                        if mtls != "off":
                            agents.cert_seen(a, der)
                        said_hello = True
                        send({"t": "welcome", "host": a.host, "heartbeat": protocol.HEARTBEAT, "server_time": utcnow().timestamp()})
                        if mtls != "off" and msg.get("certs") and (msg.get("cert_sha256") != a.cert_sha256 or
                                                                   agent_pki.needs_renewal(a.cert_not_after)):
                            # it has no certificate, not the latest one, or one that runs out within 30 days
                            send({"t": "renew"})
                    elif t == "heartbeat":
                        agents.heartbeat(a, msg)
                        if not msg.get("buffered"):
                            send({"t": "ok"})
                    elif t == "ack":
                        agents.acked(a, msg.get("id"))
                    elif t == "result":
                        agents.finished(a, msg)
                    elif t == "csr" and mtls != "off":
                        try:
                            send({"t": "cert", "cert": agents.renew_cert(a, str(msg.get("csr", ""))), "ca": agent_pki.ca_pem()})
                        except agents.AgentError as e:
                            send({"t": "cert", "error": str(e)})
                    elif t == "bundle.get":
                        data = agents.bundle(a, str(msg.get("sha256", "")))
                        send({"t": "bundle", "sha256": msg.get("sha256")} |
                             ({"data": protocol.b64(data)} if data is not None else {"error": "no such bundle for a job of this agent"}))
            if said_hello:
                for j in agents.to_send(a):
                    send(agents.signed(j))
                    j.status, j.sent_at = "sent", utcnow()
            db.session.commit()
    except Exception:  # a dropped link; the agent reconnects
        db.session.rollback()
    finally:
        gone.set()
        if wakes.get(agent_id) is not None and wakes[agent_id].inbox is inbox:
            wakes.pop(agent_id, None)
        try:
            a = db.session.get(Agent, agent_id)
            if a is not None:
                agents.unlinked(a, worker)
                db.session.commit()
        except Exception:
            db.session.rollback()
        db.session.remove()


def init_link(app) -> None:
    from flask_sock import Sock
    sock = Sock(app)
    app.config.setdefault("SOCK_SERVER_OPTIONS", {"ping_interval": 25, "max_message_size": protocol.MAX_MESSAGE})
    sock.route("/api/agents/link")(_link)
