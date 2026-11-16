"""Sites: home, public … each with its own hosts, networks and integrations."""
from __future__ import annotations

from flask import Response, g, jsonify, request

from .. import audit, inventory, rbac, sites, store
from ..models import Site, db
from ..plugins import jobs
from . import body, bp, err


def _list():
    return [sites.to_dict(s, with_usage=True) for s in sites.all_sites()]


@bp.get("/sites")
def list_sites():
    return jsonify({"sites": _list(), "default": sites.default(), "can_edit": bool(g.principal.is_admin)})


@bp.post("/sites")
@bp.put("/sites/<code>")
def save_site(code=None):
    rbac.require_admin()
    existing = db.session.get(Site, code) if code else None
    if code and existing is None:
        return err(404, f"no site '{code}'")
    before = sites.to_dict(existing) if existing else None
    try:
        s = sites.save(body(), existing)
    except store.ValidationError as exc:
        return err(422, "validation failed", exc.errors)
    audit.record("update" if existing else "create", "site", s.code, before, sites.to_dict(s))
    db.session.commit()
    jobs.emit("site.updated" if existing else "site.created")
    return jsonify({"sites": _list(), "default": sites.default()}), 200 if existing else 201


@bp.delete("/sites/<code>")
def delete_site(code):
    rbac.require_admin()
    s = db.session.get(Site, code)
    if s is None:
        return err(404, f"no site '{code}'")
    before = sites.to_dict(s)
    try:
        sites.delete(s)
    except store.ValidationError as exc:
        return err(409, exc.errors[0]["message"], exc.errors)
    audit.record("delete", "site", code, before, None)
    db.session.commit()
    jobs.emit("site.deleted")
    return jsonify({"sites": _list(), "default": sites.default()})


@bp.put("/sites/order")
def order_sites():
    rbac.require_admin()
    order = [str(c) for c in (body().get("order") or [])]
    sites.reorder(order)
    audit.record("update", "site", "order", None, {"order": order}, summary="reordered the sites")
    db.session.commit()
    return jsonify({"sites": _list(), "default": sites.default()})


@bp.get("/sites/<code>/export")
def export_site(code):
    """The site's networks and hosts as YAML: the labdex/<site>.yaml file kept in git."""
    if not sites.exists(code):
        return err(404, f"no site '{code}'")
    rbac.require_admin()  # the whole site, like the inventory backup
    text = inventory.export_yaml(site=code)
    disp = "attachment" if request.args.get("download") else "inline"
    return Response(text, mimetype="application/yaml", headers={"Content-Disposition": f'{disp}; filename="{code}.yaml"'})
