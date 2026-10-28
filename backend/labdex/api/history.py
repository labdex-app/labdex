from __future__ import annotations

from flask import jsonify, request

from .. import audit
from ..models import AuditEvent, db
from ..plugins import jobs
from . import bp, err


@bp.get("/history")
def list_history():
    q = db.select(AuditEvent).order_by(AuditEvent.id.desc())
    for field in ("object_type", "object_name", "action"):
        if request.args.get(field):
            q = q.where(getattr(AuditEvent, field) == request.args[field])
    if request.args.get("changes") == "1":  # without sign-ins and sign-outs
        q = q.where(AuditEvent.action.not_in(("login", "logout")))
    if request.args.get("actor"):
        q = q.where(AuditEvent.actor.contains(request.args["actor"]))
    if request.args.get("before"):
        q = q.where(AuditEvent.id < int(request.args["before"]))
    limit = min(int(request.args.get("limit", 50)), 200)
    out = []
    # filter in Python (permissions depend on the snapshots), page through until full
    for ev in db.session.scalars(q.limit(limit * 10)):
        if audit.visible(ev):
            out.append(audit.to_dict(ev))
            if len(out) >= limit:
                break
    resp = jsonify(out)
    if len(out) >= limit:
        resp.headers["X-Next-Before"] = str(out[-1]["id"])
    return resp


@bp.get("/history/changes")
def changes():
    """The net change per object since a point in time: ?since=7d (or 24h, 2w, 2026-10-01), &until= optional."""
    try:
        start = audit.parse_since(request.args.get("since") or "7d")
        end = audit.parse_since(request.args["until"]) if request.args.get("until") else None
    except ValueError:
        return err(422, "since and until are like 24h, 7d, 2w or a date (2026-10-01)")
    rows = audit.changes_since(start, end)
    return jsonify({"since": start.isoformat() + "Z", "until": end.isoformat() + "Z" if end else None, "objects": rows,
                    "counts": {c: sum(1 for r in rows if r["change"] == c) for c in ("created", "updated", "deleted")}})


@bp.get("/history/<int:event_id>")
def get_event(event_id):
    ev = db.session.get(AuditEvent, event_id)
    if ev is None or not audit.visible(ev):
        return err(404, "no such event")
    return jsonify(audit.to_dict(ev, with_diff=True))


@bp.post("/history/<int:event_id>/revert")
def revert_event(event_id):
    ev = db.session.get(AuditEvent, event_id)
    if ev is None or not audit.visible(ev):
        return err(404, "no such event")
    if not audit.revertible(ev):
        return err(409, "this kind of event can't be reverted")
    new = audit.revert(ev)
    db.session.commit()
    jobs.emit(f"{ev.object_type}.reverted")
    return jsonify(audit.to_dict(new, with_diff=True))
