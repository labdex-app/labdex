"""Plugin installation, trusted signing keys, signed repositories and plugin pages."""
from __future__ import annotations

from flask import current_app, g, jsonify, request

from .. import audit, naming, rbac, store
from ..models import Integration, PluginRepository, TrustedKey, db
from ..plugins import catalog, installer, resolved_settings
from ..plugins.base import Context
from ..plugins.trust import key_id, load_public, public_b64
from . import body, bp, err


def _admin():
    rbac.require_admin()


def _install_error(exc):
    return err(422, str(exc))


@bp.post("/plugins/upload")
def upload_plugin():
    _admin()
    f = request.files.get("file")
    if f is None:
        return err(422, "send the plugin as a .zip in the form field 'file'")
    try:
        return jsonify(installer.stage(f.read(), source=f.filename or "upload"))
    except installer.InstallError as exc:
        return _install_error(exc)


@bp.post("/plugins/stage/<token>/confirm")
def confirm_plugin(token):
    _admin()
    data = request.get_json(silent=True) or {}
    try:
        info = installer.confirm(token, bool(data.get("accept_unsigned")))
    except installer.InstallError as exc:
        return _install_error(exc)
    audit.record("create", "plugin", info["id"], None, {"version": info["version"], "trust": info["trust"]["status"],
                                                        "source": info["source"]},
                 summary=f"installed {info['id']} {info['version']} ({info['trust']['status']})")
    db.session.commit()
    return jsonify(info), 201


@bp.delete("/plugins/<pid>")
def uninstall_plugin(pid):
    _admin()
    spec = current_app.extensions["labdex"]["registry"].get(pid)
    if spec is not None and spec.bundled and not spec.replaces_bundled:  # it stays on disk, it's just no longer offered
        try:
            catalog.uninstall(pid)
        except ValueError as exc:
            return err(409, str(exc))
        audit.record("delete", "plugin", pid, summary=f"uninstalled {spec.name} (a development checkout: it stays on disk)")
        db.session.commit()
        return "", 204
    try:
        installer.uninstall(pid, force=request.args.get("force") == "1")
    except installer.InstallError as exc:
        return err(409, str(exc))
    audit.record("delete", "plugin", pid, summary=f"uninstalled {pid}")
    db.session.commit()
    return "", 204


@bp.post("/plugins/reload")
def reload_plugins():
    _admin()
    reg = installer.reload_registry()
    return jsonify({"plugins": len(reg.specs), "errors": reg.errors})


# ------------------------------------------------------------------- keys
def key_dict(k: TrustedKey) -> dict:
    return {"id": k.id, "name": k.name, "key_id": k.key_id, "public_key": k.public_key, "added_by": k.added_by,
            "created_at": k.created_at.isoformat() + "Z"}


@bp.get("/plugins/keys")
def list_keys():
    _admin()
    from ..plugins.trust import official_keys
    return jsonify({"official": [{"name": n, "key_id": kid, "public_key": public_b64(pub)} for kid, (n, pub) in official_keys().items()],
                    "community": [key_dict(k) for k in db.session.scalars(db.select(TrustedKey).order_by(TrustedKey.name))]})


@bp.post("/plugins/keys")
def add_key():
    _admin()
    data = body()
    name = str(data.get("name", "")).strip()[:64]
    try:
        pub = load_public(str(data.get("public_key", "")))
    except (ValueError, TypeError) as exc:
        raise store.ValidationError([{"field": "public_key", "message": "an Ed25519 public key (PEM or base64)"}]) from exc
    if not name:
        raise store.ValidationError([{"field": "name", "message": "required"}])
    kid = key_id(pub)
    if db.session.scalar(db.select(TrustedKey).where((TrustedKey.key_id == kid) | (TrustedKey.name == name))):
        raise store.ValidationError([{"field": "public_key", "message": "this key or name is already trusted"}])
    k = TrustedKey(name=name, key_id=kid, public_key=public_b64(pub), added_by=g.principal.label)
    db.session.add(k)
    db.session.flush()
    audit.record("create", "plugin-key", name, None, {"key_id": kid}, summary=f"trusts plugin key {name} ({kid})")
    db.session.commit()
    installer.reload_registry()
    return jsonify(key_dict(k)), 201


@bp.delete("/plugins/keys/<int:kid>")
def remove_key(kid):
    _admin()
    k = db.session.get(TrustedKey, kid)
    if k is None:
        return err(404, "no such key")
    audit.record("delete", "plugin-key", k.name, {"key_id": k.key_id}, None, summary=f"no longer trusts {k.name}")
    db.session.delete(k)
    db.session.commit()
    installer.reload_registry()  # plugins signed only with it stop loading
    return "", 204


# ----------------------------------------------------------- repositories
def repo_dict(r: PluginRepository) -> dict:
    return {"id": r.id, "name": r.name, "url": r.url, "key_id": r.key_id, "official": r.official, "enabled": r.enabled,
            "added_by": r.added_by, "created_at": r.created_at.isoformat() + "Z",
            "fetched_at": r.fetched_at.isoformat() + "Z" if r.fetched_at else None, "error": r.error}


@bp.get("/plugin-repos")
def list_repos():
    _admin()
    legacy = [u.strip() for u in naming.get_setting("marketplace_urls").replace(",", "\n").splitlines() if u.strip()]
    return jsonify({"repositories": [repo_dict(r) for r in db.session.scalars(db.select(PluginRepository).order_by(PluginRepository.name))],
                    "legacy_urls": legacy})


@bp.post("/plugin-repos/preview")
def preview_repo():
    """Step 1: fetch the index and show whose key it is. Nothing is saved."""
    _admin()
    url = str(body().get("url", "")).strip()
    try:
        return jsonify(installer.preview_repository(url))
    except installer.InstallError as exc:
        return _install_error(exc)


@bp.post("/plugin-repos")
def add_repo():
    """Step 2: trust it. The key id from the preview must still match (pinned from now on)."""
    _admin()
    data = body()
    url = str(data.get("url", "")).strip()
    try:
        pv = installer.preview_repository(url)
    except installer.InstallError as exc:
        return _install_error(exc)
    if str(data.get("key_id", "")) != pv["key_id"]:
        return err(409, f"the repository's key changed to {pv['key_id']} since you looked; check it again")
    if db.session.scalar(db.select(PluginRepository).where(PluginRepository.url == url)):
        raise store.ValidationError([{"field": "url", "message": "already added"}])
    name = str(data.get("name") or pv["name"]).strip()[:64]
    if db.session.scalar(db.select(PluginRepository).where(PluginRepository.name == name)):
        raise store.ValidationError([{"field": "name", "message": "a repository with this name exists"}])
    r = PluginRepository(name=name, url=url, key_id=pv["key_id"], public_key=pv["public_key"], official=pv["official"],
                         added_by=g.principal.label)
    db.session.add(r)
    db.session.flush()
    audit.record("create", "plugin-repository", name, None, {"url": url, "key_id": r.key_id},
                 summary=f"trusts plugin repository {name} ({r.key_id})")
    db.session.commit()
    installer.reload_registry()
    return jsonify(repo_dict(r)), 201


@bp.put("/plugin-repos/<int:rid>")
def update_repo(rid):
    _admin()
    r = db.session.get(PluginRepository, rid) or _404()
    data = body()
    if "enabled" in data:
        r.enabled = bool(data["enabled"])
    audit.record("update", "plugin-repository", r.name, summary=f"{'enabled' if r.enabled else 'disabled'} repository {r.name}")
    db.session.commit()
    installer.reload_registry()
    return jsonify(repo_dict(r))


@bp.delete("/plugin-repos/<int:rid>")
def remove_repo(rid):
    _admin()
    r = db.session.get(PluginRepository, rid) or _404()
    audit.record("delete", "plugin-repository", r.name, {"url": r.url, "key_id": r.key_id}, None,
                 summary=f"no longer trusts repository {r.name}")
    db.session.delete(r)
    db.session.commit()
    installer.reload_registry()  # plugins signed only with its key stop loading
    return "", 204


def _404():
    from flask import abort
    abort(404, description="no such repository")


@bp.get("/marketplace")
def marketplace():
    """What can be installed: development checkouts, and what the repositories offer."""
    if not g.principal.can_any("integration", "view"):
        return err(403, "you lack integration:view")
    return jsonify(installer.marketplace(fetch=request.args.get("offline") != "1") | {"can_install": bool(g.principal.is_admin)})


@bp.post("/marketplace/install")
def marketplace_install():
    _admin()
    data = body()
    pid = str(data.get("id", ""))
    spec = current_app.extensions["labdex"]["registry"].get(pid)
    if spec is not None and spec.bundled and not data.get("repository_id"):  # a development checkout: nothing to download
        if catalog.is_installed(spec):
            return jsonify({"id": pid, "installed": True})
        catalog.install(pid)
        audit.record("create", "plugin", pid, None, {"version": str(spec.manifest.get("version", "")), "source": "development checkout"},
                     summary=f"installed {spec.name} {spec.manifest.get('version', '')} (development checkout)")
        db.session.commit()
        return jsonify({"id": pid, "installed": True, "name": spec.name}), 201
    r = db.session.get(PluginRepository, int(data.get("repository_id") or 0))
    if r is None or not r.enabled:
        return err(422, "no such repository")
    try:
        return jsonify(installer.install_from_repository(r, str(data.get("id", "")), str(data.get("version") or "") or None))
    except installer.InstallError as exc:
        return _install_error(exc)


# ------------------------------------------------------------------ updates
@bp.post("/plugins/<pid>/rollback")
def rollback_plugin(pid):
    """Back to the version the last update replaced (kept on disk); again to undo."""
    _admin()
    spec = current_app.extensions["labdex"]["registry"].get(pid)
    was = str(spec.manifest.get("version", "")) if spec else ""
    try:
        r = installer.rollback(pid)
    except installer.InstallError as exc:
        return _install_error(exc)
    audit.record("update", "plugin", pid, {"version": was}, {"version": r["version"]}, summary=f"rolled {pid} back from {was} to {r['version']}")
    db.session.commit()
    return jsonify(r)


@bp.put("/plugins/<pid>/hold")
def hold_plugin(pid):
    """{"version": "1.2"} stays on that version (no update offered or installed); {"version": null} lets it go."""
    from ..plugins import updates
    _admin()
    spec = current_app.extensions["labdex"]["registry"].get(pid)
    if spec is None:
        return err(404, f"no plugin '{pid}'")
    v = str(body().get("version") or "").strip()[:40] or None
    updates.hold(pid, v)
    audit.record("update", "plugin", pid, summary=f"holds {spec.name} at {v}" if v else f"lets {spec.name} update again")
    db.session.commit()
    return jsonify({"id": pid, "held": v or ""})


@bp.get("/plugin-updates")
def get_plugin_updates():
    from ..plugins import updates
    if not g.principal.can_any("integration", "view"):
        return err(403, "you lack integration:view")
    return jsonify({"mode": updates.mode(), "modes": list(updates.MODES), "holds": updates.holds(),
                    "checked": updates._get(updates.LAST, ""), "can_edit": bool(g.principal.is_admin)})


@bp.put("/plugin-updates")
def put_plugin_updates():
    from ..plugins import updates
    _admin()
    m = str(body().get("mode", ""))
    try:
        updates.set_mode(m)
    except ValueError as exc:
        return err(422, "validation failed", [{"field": "mode", "message": str(exc)}])
    audit.record("update", "setting", "plugin updates", summary=f"plugin updates: {m}")
    db.session.commit()
    return get_plugin_updates()


@bp.post("/plugins/restore")
def restore_plugins():
    """Install the plugins this labdex uses but doesn't have (after the upgrade from 1.24.0) now."""
    _admin()
    return jsonify(installer.restore_missing(force=True))


@bp.post("/plugin-updates/check")
def check_plugin_updates():
    """Check now (also installs official updates in "official" mode)."""
    from ..plugins import updates
    _admin()
    return jsonify(updates.check(force=True))


# ------------------------------------------------------------------ pages
@bp.get("/integrations/<name>/pages/<page>")
def plugin_page(name, page):
    integ = db.session.scalar(db.select(Integration).where(Integration.name == name))
    if integ is None or not g.principal.can("integration", "view", rbac.named_facts(name)):
        return err(404, f"no integration '{name}'")
    spec = current_app.extensions["labdex"]["registry"].get(integ.plugin_id)
    if spec is None or spec.error or page not in {p["id"] for p in spec.pages}:
        return err(404, f"no page '{page}'")
    if not integ.enabled:
        return err(409, "the integration is disabled")
    vault = current_app.extensions["labdex"]["vault"]
    ctx = Context(integ.name, spec, resolved_settings(spec, integ.settings or {}, vault), lambda m: None, integration=integ)
    fn = getattr(spec.plugin_class()(ctx), f"page_{page}", None) if spec.plugin_class() else None
    if fn is None:
        return err(404, f"plugin has no page_{page}()")
    return jsonify(fn(dict(request.args)))
