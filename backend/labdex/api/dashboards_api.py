"""Dashboards: shared ones (administrators change them) and your own."""
from __future__ import annotations

from flask import g, jsonify

from .. import dashboards
from ..models import Dashboard, db, utcnow
from . import body, bp, err


def _find(uid) -> Dashboard | None:
    d = db.session.scalar(db.select(Dashboard).where(Dashboard.uid == str(uid)))
    if d is None or d not in dashboards.visible(g.principal):
        return None
    return d


@bp.get("/dashboards")
def list_dashboards():
    p = g.principal
    return jsonify({"dashboards": [dashboards.to_dict(d, p, with_items=False) for d in dashboards.visible(p)],
                    "types": dashboards.TYPES, "can_share": bool(p.is_admin), "can_own": p.user is not None})


@bp.get("/dashboards/<uid>")
def get_dashboard(uid):
    d = _find(uid)
    if d is None:
        return err(404, "no such dashboard")
    return jsonify(dashboards.to_dict(d, g.principal))


@bp.post("/dashboards")
def create_dashboard():
    p = g.principal
    data = body()
    name = str(data.get("name") or "").strip()[:64]
    if not name:
        return err(422, "validation failed", [{"field": "name", "message": "required"}])
    shared = bool(data.get("shared"))
    if shared and not p.is_admin:
        return err(403, "only administrators make shared dashboards")
    if not shared and p.user is None:
        return err(403, "a dashboard of your own needs a user")
    items, errors = dashboards.clean_items(data.get("items") or [])
    if errors:
        return err(422, "validation failed", errors)
    owner = "" if shared else p.user.username
    pos = (db.session.scalar(db.select(db.func.max(Dashboard.position)).where(Dashboard.owner == owner)) or 0) + 1
    d = Dashboard(name=name, owner=owner, items=items, position=pos, updated_by=p.label, updated_at=utcnow())
    db.session.add(d)
    db.session.commit()
    return jsonify(dashboards.to_dict(d, p)), 201


@bp.put("/dashboards/<uid>")
def update_dashboard(uid):
    p = g.principal
    d = _find(uid)
    if d is None:
        return err(404, "no such dashboard")
    if not dashboards.can_edit(d, p):
        return err(403, "only administrators change shared dashboards" if d.owner == "" else "not your dashboard")
    data = body()
    if "name" in data:
        name = str(data.get("name") or "").strip()[:64]
        if not name:
            return err(422, "validation failed", [{"field": "name", "message": "required"}])
        d.name = name
    if "items" in data:
        items, errors = dashboards.clean_items(data["items"])
        if errors:
            return err(422, "validation failed", errors)
        d.items = items
    if "position" in data:
        try:
            d.position = int(data["position"])
        except (TypeError, ValueError):
            return err(422, "validation failed", [{"field": "position", "message": "a number"}])
    d.updated_by, d.updated_at = p.label, utcnow()
    db.session.commit()
    return jsonify(dashboards.to_dict(d, p))


@bp.delete("/dashboards/<uid>")
def delete_dashboard(uid):
    p = g.principal
    d = _find(uid)
    if d is None:
        return err(404, "no such dashboard")
    if not dashboards.can_edit(d, p):
        return err(403, "only administrators change shared dashboards" if d.owner == "" else "not your dashboard")
    db.session.delete(d)
    db.session.commit()
    return "", 204
