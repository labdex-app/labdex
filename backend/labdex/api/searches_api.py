"""Saved searches of the Hosts page: shared ones (administrators change them) and your own."""
from __future__ import annotations

from flask import g, jsonify
from sqlalchemy import or_

from ..models import SavedSearch, db, utcnow
from . import body, bp, err

KEYS = ("q", "zone", "cat", "status", "tag", "kind", "group")


def _clean(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {k: str(raw[k]).strip()[:200] for k in KEYS if raw.get(k) not in (None, "")}


def _dict(s: SavedSearch, p) -> dict:
    return {"uid": s.uid, "name": s.name, "query": s.query or {}, "shared": s.owner == "",
            "owner": s.owner, "can_edit": _can_edit(s, p)}


def _can_edit(s: SavedSearch, p) -> bool:
    return p.is_admin if s.owner == "" else p.user is not None and s.owner == p.user.username


def _find(uid) -> SavedSearch | None:
    p = g.principal
    s = db.session.scalar(db.select(SavedSearch).where(SavedSearch.uid == str(uid)))
    if s is None or (s.owner != "" and (p.user is None or s.owner != p.user.username)):
        return None
    return s


@bp.get("/searches")
def list_searches():
    p = g.principal
    mine = p.user.username if p.user else None
    q = db.select(SavedSearch).where(or_(SavedSearch.owner == "", SavedSearch.owner == mine)).order_by(SavedSearch.name)
    return jsonify({"searches": [_dict(s, p) for s in db.session.scalars(q)],
                    "can_share": bool(p.is_admin), "can_own": p.user is not None})


@bp.post("/searches")
def create_search():
    p = g.principal
    data = body()
    name = str(data.get("name") or "").strip()[:64]
    query = _clean(data.get("query"))
    errors = []
    if not name:
        errors.append({"field": "name", "message": "required"})
    if not query:
        errors.append({"field": "query", "message": "nothing to save: type a search or pick a filter first"})
    if errors:
        return err(422, "validation failed", errors)
    shared = bool(data.get("shared"))
    if shared and not p.is_admin:
        return err(403, "only administrators make shared searches")
    if not shared and p.user is None:
        return err(403, "a search of your own needs a user")
    owner = "" if shared else p.user.username
    if db.session.scalar(db.select(SavedSearch).where(SavedSearch.owner == owner, SavedSearch.name == name)):
        return err(422, "validation failed", [{"field": "name", "message": "there's a saved search with that name"}])
    s = SavedSearch(name=name, owner=owner, query=query, updated_by=p.label, updated_at=utcnow())
    db.session.add(s)
    db.session.commit()
    return jsonify(_dict(s, p)), 201


@bp.put("/searches/<uid>")
def update_search(uid):
    p = g.principal
    s = _find(uid)
    if s is None:
        return err(404, "no such saved search")
    if not _can_edit(s, p):
        return err(403, "only administrators change shared searches" if s.owner == "" else "not your search")
    data = body()
    if "name" in data:
        name = str(data["name"] or "").strip()[:64]
        if not name:
            return err(422, "validation failed", [{"field": "name", "message": "required"}])
        other = db.session.scalar(db.select(SavedSearch).where(SavedSearch.owner == s.owner, SavedSearch.name == name))
        if other is not None and other is not s:
            return err(422, "validation failed", [{"field": "name", "message": "there's a saved search with that name"}])
        s.name = name
    if "query" in data:
        query = _clean(data["query"])
        if not query:
            return err(422, "validation failed", [{"field": "query", "message": "nothing to save"}])
        s.query = query
    s.updated_by, s.updated_at = p.label, utcnow()
    db.session.commit()
    return jsonify(_dict(s, p))


@bp.delete("/searches/<uid>")
def delete_search(uid):
    s = _find(uid)
    if s is None:
        return err(404, "no such saved search")
    if not _can_edit(s, g.principal):
        return err(403, "only administrators change shared searches" if s.owner == "" else "not your search")
    db.session.delete(s)
    db.session.commit()
    return "", 204
