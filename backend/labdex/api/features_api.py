"""The Ansible feature tree."""
from __future__ import annotations

from flask import g, jsonify

from .. import audit, features, rbac
from ..models import Feature, Host, db
from ..plugins import jobs
from . import body, bp, err


def _can_view():
    if not (g.principal.can_any("host", "view")):
        rbac.require("host", "view")


def _can_edit():
    # features change what runs on hosts everywhere: needs host:admin somewhere, like an inventory-wide change
    if not g.principal.is_admin and not g.principal.can("host", "admin", rbac.Facts.make("*", ["*"])):
        rbac.require_admin()


@bp.get("/features")
def list_features():
    _can_view()
    feats = features.all_features()
    usage: dict[str, int] = {}
    for h in db.session.scalars(db.select(Host)):
        for p in features.expanded(h.features or []):
            usage[p] = usage.get(p, 0) + 1
    out = []
    for p, f in feats.items():
        d = features.to_dict(f)
        d["hosts"] = usage.get(p, 0)
        d["missing_parent"] = [c for c in features.chain(p)[:-1] if c not in feats]
        out.append(d)
    return jsonify(out)


@bp.get("/features/<path>")
def get_feature(path):
    _can_view()
    f = db.session.scalar(db.select(Feature).where(Feature.path == path))
    if f is None:
        return err(404, "no such feature")
    d = features.to_dict(f, with_usage=True)
    feats = features.all_features()
    merged = {}
    for c in features.chain(path):
        if c in feats:
            merged = features.deep_merge(merged, feats[c].vars or {})
    d["merged_vars"] = merged
    d["chain"] = [{"path": c, "exists": c in feats, "playbooks": feats[c].playbooks if c in feats else [],
                   "roles": feats[c].roles if c in feats else []} for c in features.chain(path)]
    d["children"] = [p for p in feats if p.startswith(path + ".")]
    d["can_edit"] = g.principal.is_admin or g.principal.can("host", "admin", rbac.Facts.make("*", ["*"]))
    return jsonify(d)


@bp.post("/features")
@bp.put("/features/<path>")
def save_feature(path=None):
    _can_edit()
    existing = None
    if path:
        existing = db.session.scalar(db.select(Feature).where(Feature.path == path))
        if existing is None:
            return err(404, "no such feature")
    before = features.to_dict(existing) if existing else None
    f = features.save(body(), existing)
    db.session.flush()
    for parent in features.ensure_parents(f.path):  # kube.cluster.oak brings kube and kube.cluster
        audit.record("create", "feature", parent.path, None, features.to_dict(parent), summary=f"made for {f.path}")
    after = features.to_dict(f)
    audit.record("update" if existing else "create", "feature", f.path, before, after)
    db.session.commit()
    jobs.emit("feature.updated")
    return jsonify(features.to_dict(f, with_usage=True)), 200 if existing else 201


@bp.delete("/features/<path>")
def delete_feature(path):
    _can_edit()
    f = db.session.scalar(db.select(Feature).where(Feature.path == path))
    if f is None:
        return err(404, "no such feature")
    before = features.to_dict(f)
    features.delete(f)
    audit.record("delete", "feature", path, before, None)
    db.session.commit()
    jobs.emit("feature.deleted")
    return "", 204
