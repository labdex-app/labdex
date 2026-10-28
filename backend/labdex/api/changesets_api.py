"""Change sets: inventory changes reviewed as one unit (see changesets.py)."""
from __future__ import annotations

import yaml
from flask import g, jsonify

from .. import changesets, store
from ..models import ChangeSet, db, utcnow
from ..plugins import jobs
from . import body, bp, err


def _need_edit() -> None:
    p = g.principal
    if not (p.can_any("host", "edit") or p.can_any("network", "edit") or p.can_any("alias", "edit")):
        from flask import abort
        abort(403, description="change sets are for people who edit the inventory")


def _dict(cs: ChangeSet, with_patch: bool = True) -> dict:
    ops = changesets.operations(cs.patch or {})
    d = {"uid": cs.uid, "name": cs.name, "description": cs.description, "status": cs.status, "created_by": cs.created_by,
         "updated_at": cs.updated_at.isoformat() + "Z" if cs.updated_at else None, "applied_by": cs.applied_by,
         "applied_at": cs.applied_at.isoformat() + "Z" if cs.applied_at else None,
         "counts": {"networks": sum(1 for o in ops if o["type"] == "network"), "hosts": sum(1 for o in ops if o["type"] == "host"),
                    "aliases": sum(1 for o in ops if o["type"] == "alias"), "deletes": sum(1 for o in ops if o["op"] == "delete")}}
    if with_patch:
        d["yaml"] = yaml.safe_dump(cs.patch or {}, sort_keys=False, allow_unicode=True)
    return d


def _find(uid) -> ChangeSet | None:
    return db.session.scalar(db.select(ChangeSet).where(ChangeSet.uid == str(uid)))


def _patch_from(data: dict) -> dict:
    try:
        return changesets.parse(str(data.get("yaml") or ""))
    except changesets.PatchError as exc:
        raise store.ValidationError(exc.errors) from exc


def _name_free(name: str, cs: ChangeSet | None) -> None:
    other = db.session.scalar(db.select(ChangeSet).where(ChangeSet.name == name))
    if other is not None and other is not cs:
        raise store.ValidationError([{"field": "name", "message": "there's a change set with that name"}])


@bp.get("/changesets")
def list_changesets():
    _need_edit()
    rows = db.session.scalars(db.select(ChangeSet).order_by(ChangeSet.status, ChangeSet.updated_at.desc()))
    return jsonify([_dict(c, with_patch=False) for c in rows])


@bp.post("/changesets")
def create_changeset():
    _need_edit()
    data = body()
    name = str(data.get("name") or "").strip()[:64]
    if not name:
        raise store.ValidationError([{"field": "name", "message": "required"}])
    _name_free(name, None)
    cs = ChangeSet(name=name, description=str(data.get("description") or "")[:2000], patch=_patch_from(data),
                   created_by=g.principal.label, updated_at=utcnow())
    db.session.add(cs)
    db.session.commit()
    return jsonify(_dict(cs)), 201


@bp.get("/changesets/<uid>")
def get_changeset(uid):
    _need_edit()
    cs = _find(uid)
    return jsonify(_dict(cs)) if cs else err(404, "no such change set")


@bp.put("/changesets/<uid>")
def update_changeset(uid):
    _need_edit()
    cs = _find(uid)
    if cs is None:
        return err(404, "no such change set")
    if cs.status != "draft":
        return err(409, "an applied change set is a record; make a new one")
    data = body()
    if "name" in data:
        name = str(data["name"] or "").strip()[:64]
        if not name:
            raise store.ValidationError([{"field": "name", "message": "required"}])
        _name_free(name, cs)
        cs.name = name
    if "description" in data:
        cs.description = str(data["description"] or "")[:2000]
    if "yaml" in data:
        cs.patch = _patch_from(data)
    cs.updated_at = utcnow()
    db.session.commit()
    return jsonify(_dict(cs))


@bp.delete("/changesets/<uid>")
def delete_changeset(uid):
    _need_edit()
    cs = _find(uid)
    if cs is None:
        return err(404, "no such change set")
    if cs.status != "draft":
        return err(409, "an applied change set stays as a record")
    db.session.delete(cs)
    db.session.commit()
    return "", 204


@bp.post("/changesets/<uid>/preview")
def preview_changeset(uid):
    """Apply it, compare lint, aliases and DNS before and after, roll everything back."""
    _need_edit()
    cs = _find(uid)
    if cs is None:
        return err(404, "no such change set")
    if cs.status != "draft":
        return err(409, "already applied")
    return jsonify(changesets.run(cs.patch, cs.name, g.principal))


@bp.post("/changesets/<uid>/apply")
def apply_changeset(uid):
    """Apply it for real: all of it or none. It is checked again now, so what changed since the preview counts."""
    _need_edit()
    cs = _find(uid)
    if cs is None:
        return err(404, "no such change set")
    if cs.status != "draft":
        return err(409, "already applied")
    name, patch, uid_ = cs.name, cs.patch, cs.uid
    report = changesets.run(patch, name, g.principal, apply=True)
    if not report["applied"]:
        return jsonify(report), 422
    cs = _find(uid_)
    cs.status, cs.applied_by, cs.applied_at = "applied", g.principal.label, utcnow()
    db.session.commit()
    for ev in ("network.updated", "host.updated", "alias.updated"):
        jobs.emit(ev)
    return jsonify(report)
