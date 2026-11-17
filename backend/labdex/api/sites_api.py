"""Sites: home, public … each with its own hosts, networks and integrations."""
from __future__ import annotations

from flask import Response, current_app, g, jsonify, request

from .. import audit, inventory, rbac, sites, store
from ..models import Site, db
from ..plugins import jobs
from . import body, bp, err


def _list():
    from .. import gitsource
    admin = bool(getattr(g, "principal", None) and g.principal.is_admin)
    out = []
    for s in sites.all_sites():
        d = sites.to_dict(s, with_usage=True)
        if admin:  # where the site's file lives, and the webhook secret to give GitLab or GitHub
            d["git"] = gitsource.public(s)
            if d["git"]:
                d["git"]["hook_secret"] = s.git.get("hook_secret", "")
        out.append(d)
    return out


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


@bp.post("/sites/<code>/git/sync")
def sync_site_git(code):
    """Sync the site with its git source now; {prefer: git | labdex} settles a conflict."""
    from .. import gitsource
    rbac.require_admin()
    s = db.session.get(Site, code)
    if s is None or gitsource.config(s) is None:
        return err(404, f"site '{code}' has no git source")
    prefer = str(body().get("prefer") or "") or None
    if prefer not in (None, "git", "labdex"):
        return err(422, "prefer: git or labdex")
    out = gitsource.sync(code, prefer=prefer, actor=audit._actor())
    if out.get("plan"):
        from .. import fileplan
        out["plan_markdown"] = fileplan.markdown(out.pop("plan"))
    return jsonify(out | {"sites": _list()}), 409 if out["action"] == "conflict" else 502 if out["action"] == "error" else 200


@bp.post("/git/hook/<code>")
def git_hook(code):
    """A push webhook from GitLab or GitHub (no login: the site's secret signs it). The next round
    of the job worker pulls the new commit."""
    from .. import gitsource
    s = db.session.get(Site, code)
    if s is None or not gitsource.hook_ok(s, request.headers, request.get_data()):
        return err(403, "unknown site or wrong secret")
    gitsource.request_pull(s)
    if current_app.config.get("LABDEX_JOBS_SYNC"):
        gitsource.tick()
    return jsonify({"queued": True}), 202
