"""Tags known everywhere: hosts pick from them, and a new one is made when first used."""
from __future__ import annotations

import re

from flask import g, jsonify

from .. import audit, rbac, store
from ..models import Host, RoleBinding, Tag, db
from ..plugins import jobs
from . import body, bp, err

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for e in store.host_index():
        if g.principal.can("host", "view", e["facts"]):
            for t in e["host"]["tags"]:
                out[t] = out.get(t, 0) + 1
    return out


def tag_dict(t: Tag, counts: dict) -> dict:
    return {"name": t.name, "colour": t.colour or "", "description": t.description or "", "hosts": counts.get(t.name, 0)}


@bp.get("/tags")
def list_tags():
    counts = _counts()
    return jsonify([tag_dict(t, counts) for t in db.session.scalars(db.select(Tag).order_by(Tag.name))])


@bp.post("/tags")
@bp.put("/tags/<name>")
def save_tag(name=None):
    """Create a tag (anyone who may edit hosts), or change one: colour and description, or a
    rename that follows every host and role scope (admins)."""
    data = body()
    existing = db.session.get(Tag, name) if name else None
    if name and existing is None:
        return err(404, f"no tag '{name}'")
    if existing is None and not g.principal.can_any("host", "edit"):
        return err(403, "needs host:edit")
    new = str(data.get("name", name or "")).strip().lower()
    errors = []
    if not store.TAG_RE.match(new):
        errors.append({"field": "name", "message": "lowercase letters, digits and - _ . : (at most 32)"})
    elif new != name and db.session.get(Tag, new) is not None:
        errors.append({"field": "name", "message": "already exists"})
    colour = str(data.get("colour") or "").strip().lower()
    if colour and not HEX_RE.match(colour):
        errors.append({"field": "colour", "message": "a colour like #4ade80"})
    if errors:
        raise store.ValidationError(errors)
    if existing is not None:
        rbac.require_admin()
    before = {"name": existing.name, "colour": existing.colour, "description": existing.description} if existing else None
    if existing is not None and new != existing.name:
        old = existing.name
        t = Tag(name=new)
        db.session.add(t)
        for h in db.session.scalars(db.select(Host)):
            if old in (h.tags or []):
                h.tags = sorted({new if x == old else x for x in h.tags})
        for b in db.session.scalars(db.select(RoleBinding)):
            b.scopes = [f"tag:{new}" if sc == f"tag:{old}" else sc for sc in b.scopes or []]
        db.session.flush()
        db.session.delete(existing)
    else:
        t = existing or Tag(name=new)
        if existing is None:
            db.session.add(t)
    t.colour, t.description = colour, str(data.get("description", ""))
    db.session.flush()
    after = {"name": t.name, "colour": t.colour, "description": t.description}
    audit.record("update" if existing else "create", "tag", t.name, before, after)
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify(tag_dict(t, _counts())), 200 if existing else 201


@bp.delete("/tags/<name>")
def delete_tag(name):
    """Removes the tag from every host too."""
    rbac.require_admin()
    t = db.session.get(Tag, name)
    if t is None:
        return err(404, f"no tag '{name}'")
    n = 0
    for h in db.session.scalars(db.select(Host)):
        if name in (h.tags or []):
            h.tags = [x for x in h.tags if x != name]
            n += 1
    db.session.delete(t)
    audit.record("delete", "tag", name, {"name": name, "hosts": n}, None, summary=f"deleted tag {name} (from {n} host(s))")
    db.session.commit()
    jobs.emit("host.updated")
    return "", 204
