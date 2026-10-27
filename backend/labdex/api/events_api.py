"""Server-Sent Events: live job output and plan counts, instead of polling.

One stream per browser tab: GET /api/events[?job=<id>]. Events:

    pending   {"count": n}                       plans waiting for review (visible to you)
    job       {...job summary...}                when the watched job changes state
    log       {"text": "...new lines..."}        new output of the watched job
    revision  {"rev": n}                         something changed (newest history event); the UI drops its cache

The stream ends after a few minutes; EventSource reconnects by itself.
"""
from __future__ import annotations

import json
import threading
import time

from flask import Response, current_app, g, request, stream_with_context

from .. import rbac
from ..models import AuditEvent, Job, db
from . import bp, err
from .plugins_api import job_dict

TICK = 0.5
LIFETIME = 300
HEARTBEAT = 15
MAX_STREAMS = 8  # per user: each stream holds a server thread

# per user, the open streams' stop signals, oldest first. A stream of a closed tab only notices
# at its next write, so a new stream past the limit stops the oldest instead of being refused
# (reloading a page a few times quickly must not lock someone out of live updates).
_open: dict[int, list[threading.Event]] = {}
_open_lock = threading.Lock()


def _still_allowed(p) -> bool:
    """A stream outlives the request that opened it: the user must still be active, the API
    token still valid, the password (and so the session) unchanged."""
    from .. import auth
    from ..models import ApiToken, User, utcnow
    u = db.session.get(User, p.user.id)
    if u is None or not u.active:
        return False
    if p.via == "session" and p.session_pwv and auth._password_version(u) != p.session_pwv:
        return False
    if p.via == "token" and p.token_id:
        t = db.session.get(ApiToken, p.token_id)
        if t is None or (t.expires_at and t.expires_at < utcnow()):
            return False
    return True


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _pending(p) -> int:
    n = 0
    for j in db.session.scalars(db.select(Job).where(Job.status == "planned")):
        if j.integration and p.can("integration", "view", rbac.named_facts(j.integration.name)):
            n += 1
    return n


@bp.get("/events")
def events():
    p = g.principal
    job_id = request.args.get("job", type=int)
    if job_id is not None:
        j = db.session.get(Job, job_id)
        if j is None or not j.integration or not p.can("integration", "view", rbac.named_facts(j.integration.name)):
            return err(404, "no such job")
    once = request.args.get("once") == "1" and current_app.config.get("TESTING")
    uid = p.user.id
    stop = threading.Event()
    with _open_lock:
        mine = _open.setdefault(uid, [])
        while len(mine) >= MAX_STREAMS:
            mine.pop(0).set()
        mine.append(stop)
    lifetime = 0 if once else LIFETIME

    def gen():
        start = last_beat = time.monotonic()
        last_pending, last_state, offset, last_rev = None, None, 0, None
        try:
            while True:
                db.session.expire_all()
                pend = _pending(p)
                if pend != last_pending:
                    last_pending = pend
                    yield _sse("pending", {"count": pend})
                rev = db.session.scalar(db.select(db.func.max(AuditEvent.id))) or 0
                if rev != last_rev:
                    last_rev = rev
                    yield _sse("revision", {"rev": rev})
                if job_id is not None:
                    j = db.session.get(Job, job_id)
                    log = j.log or ""
                    if len(log) > offset:
                        yield _sse("log", {"text": log[offset:]})
                        offset = len(log)
                    state = (j.status, json.dumps(j.result, sort_keys=True), len(j.plan or []))
                    if state != last_state:
                        last_state = state
                        yield _sse("job", job_dict(j, full=True) | {"log": None})
                now = time.monotonic()
                if stop.is_set():  # replaced by a newer stream of this user
                    return
                if now - start >= lifetime:
                    yield _sse("bye", {"reconnect": True})
                    return
                if now - last_beat >= HEARTBEAT:
                    last_beat = now
                    if not _still_allowed(p):
                        yield _sse("bye", {"reconnect": False})
                        return
                    yield ": keep-alive\n\n"
                db.session.remove()  # don't hold a connection while sleeping
                time.sleep(TICK)
        finally:
            db.session.remove()
            with _open_lock:
                if stop in _open.get(uid, []):
                    _open[uid].remove(stop)

    resp = Response(stream_with_context(gen()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"  # nginx: don't buffer
    return resp
