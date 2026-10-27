"""The KV vault over HTTP. Names and field names are listed to whoever may see them; values only
come back from /reveal, one field at a time, each time written to the history (never the value).

    secret:view    names, paths, field names, versions (also while sealed)
    secret:apply   reveal values
    secret:edit    create, change, delete entries
    administrators set the vault up, unseal and seal it, change its passphrase

Scopes: an entry of a host is matched like that host (zone, tags, name …); any other entry by
its path (name:wifi/*). "*" permissions never cover reveal or edit.
"""
from __future__ import annotations

import time

from flask import g, jsonify, request, session

from .. import audit, keyvault, rbac, secretstore, security, store
from ..models import VaultEntry, VaultVersion, db
from . import body, bp, err

REAUTH_SECONDS = 300


_facts_for = keyvault.facts_for
_facts = keyvault.facts


def _can(action: str, e: VaultEntry) -> bool:
    return g.principal.can("secret", action, _facts(e))


def _dict(e: VaultEntry) -> dict:
    return {"uid": e.uid, "path": e.path, "host": e.host.name if e.host else "", "host_uid": e.host.uid if e.host else "",
            "description": e.description or "", "fields": list(e.fields or []), "version": e.version,
            "updated_by": e.updated_by or "", "updated_at": e.updated_at.isoformat() + "Z" if e.updated_at else None,
            "can_reveal": _can("apply", e), "can_edit": _can("edit", e)}


def _snapshot(e: VaultEntry) -> dict:
    """What the history keeps of an entry: never a value."""
    return {"path": e.path, "host": e.host.name if e.host else "", "description": e.description or "", "fields": list(e.fields or []),
            "version": e.version}


def _fail(exc: keyvault.VaultError):
    if exc.field:
        return err(exc.status if exc.status != 422 else 422, "validation failed", [{"field": exc.field, "message": str(exc)}])
    return err(exc.status, str(exc))


def _find(uid) -> VaultEntry | None:
    e = db.session.scalar(db.select(VaultEntry).where(VaultEntry.uid == str(uid)))
    return e if e is not None and _can("view", e) else None


def _host(name):
    if not name:
        return None
    h = store.find_host(str(name))
    if h is None:
        raise keyvault.VaultError(f"no host '{name}'", field="host")
    return h


# ------------------------------------------------------------------ the vault itself
@bp.get("/vault")
def vault_status():
    p = g.principal
    st = keyvault.status()
    return jsonify(st | {"can_unseal": bool(p.is_admin), "can_add": p.can_any("secret", "edit"),
                         "reauth_needed": _reauth_needed(), "legacy": secretstore.legacy_count()})


@bp.post("/vault/init")
def vault_init():
    rbac.require_admin()
    try:
        keyvault.init(str(body().get("passphrase") or ""))
    except keyvault.VaultError as exc:
        return _fail(exc)
    audit.record("create", "setting", "vault", summary="set up the vault")
    _move_legacy()
    db.session.commit()
    return jsonify(keyvault.status())


@bp.post("/vault/unseal")
def vault_unseal():
    rbac.require_admin()
    try:
        keyvault.unseal(str(body().get("passphrase") or ""))
    except keyvault.VaultError as exc:
        audit.record("update", "setting", "vault", summary="unseal refused: wrong passphrase" if exc.status == 403 else f"unseal refused: {exc}")
        db.session.commit()
        return _fail(exc)
    audit.record("update", "setting", "vault", summary="unsealed the vault")
    _move_legacy()
    db.session.commit()
    return jsonify(keyvault.status())


def _move_legacy() -> None:
    """The global secrets of 1.17 and before go into the vault the first time it's open."""
    moved = secretstore.migrate_legacy(g.principal.label)
    if moved:
        audit.record("update", "setting", "vault", summary=f"moved {len(moved)} global secret(s) into the vault under global/: {', '.join(moved)}")


@bp.post("/vault/seal")
def vault_seal():
    rbac.require_admin()
    keyvault.seal()
    audit.record("update", "setting", "vault", summary="sealed the vault")
    db.session.commit()
    return jsonify(keyvault.status())


@bp.post("/vault/passphrase")
def vault_passphrase():
    rbac.require_admin()
    d = body()
    try:
        keyvault.change_passphrase(str(d.get("current") or ""), str(d.get("new") or ""))
    except keyvault.VaultError as exc:
        return _fail(exc)
    audit.record("update", "setting", "vault", summary="changed the vault passphrase")
    db.session.commit()
    return jsonify(keyvault.status())


# ------------------------------------------------------------- confirming who you are
def _reauth_needed() -> bool:
    """A browser session of a user with a password confirms it before revealing (every 5 minutes)."""
    p = g.principal
    if p.via != "session" or not (p.user and p.user.password_hash):
        return False
    return time.time() - float(session.get("reauth_at") or 0) > REAUTH_SECONDS


@bp.post("/auth/confirm")
def auth_confirm():
    p = g.principal
    if p.via != "session":
        return err(400, "only browser sessions confirm their password")
    if not security.verify_password(p.user.password_hash, str(body().get("password") or "")):
        return err(422, "validation failed", [{"field": "password", "message": "wrong password"}])
    session["reauth_at"] = time.time()
    return "", 204


# ------------------------------------------------------------------ entries
@bp.get("/vault/entries")
def vault_entries():
    q = db.select(VaultEntry).order_by(VaultEntry.path)
    host = request.args.get("host")
    if host:
        h = store.find_host(host)
        if h is None:
            return jsonify({"entries": []})
        q = q.where(VaultEntry.host_id == h.id)
    text = (request.args.get("q") or "").strip().lower()
    out = [_dict(e) for e in db.session.scalars(q) if _can("view", e)]
    if text:
        out = [x for x in out if text in x["path"] or text in x["description"].lower() or text in x["host"]]
    return jsonify({"entries": out})


@bp.post("/vault/entries")
def vault_create():
    d = body()
    p = g.principal
    try:
        host = _host(d.get("host"))
        path = keyvault.clean_path(d.get("path"))
        if not p.can("secret", "edit", _facts_for(path, host)):
            return err(403, "you may not add secrets there")
        e = keyvault.create(path, d.get("fields") or {}, p.label, host=host, description=d.get("description") or "")
    except keyvault.VaultError as exc:
        db.session.rollback()
        return _fail(exc)
    audit.record("create", "secret", e.path, None, _snapshot(e))
    db.session.commit()
    return jsonify(_dict(e)), 201


@bp.get("/vault/entries/<uid>")
def vault_entry(uid):
    e = _find(uid)
    if e is None:
        return err(404, "no such secret")
    versions = db.session.scalars(db.select(VaultVersion).where(VaultVersion.entry_id == e.id).order_by(VaultVersion.version.desc()))
    return jsonify(_dict(e) | {"used_by": secretstore.used_by(e.path), "versions": [{"version": v.version, "fields": list(v.fields or []), "by": v.created_by,
                                             "at": v.created_at.isoformat() + "Z"} for v in versions]})


@bp.put("/vault/entries/<uid>")
def vault_update(uid):
    e = _find(uid)
    if e is None:
        return err(404, "no such secret")
    if not _can("edit", e):
        return err(403, "you may not change this secret")
    d = body()
    before = _snapshot(e)
    try:
        host = _host(d.get("host")) if "host" in d else False
        keyvault.update(e, g.principal.label, path=d.get("path"), fields=d.get("fields"), host=host, description=d.get("description"))
        if not _can("edit", e):  # moved somewhere you can't edit: no
            db.session.rollback()
            return err(403, "you may not move the secret there")
    except keyvault.VaultError as exc:
        db.session.rollback()
        return _fail(exc)
    if e.path != before["path"]:  # whatever points at it follows
        secretstore.rename(before["path"], e.path)
    audit.record("update", "secret", e.path, before, _snapshot(e))
    db.session.commit()
    return jsonify(_dict(e))


@bp.delete("/vault/entries/<uid>")
def vault_delete(uid):
    e = _find(uid)
    if e is None:
        return err(404, "no such secret")
    if not _can("edit", e):
        return err(403, "you may not delete this secret")
    users = secretstore.used_by(e.path)
    if users:
        return err(409, f"still used by {', '.join(users)}: point them elsewhere first")
    before = _snapshot(e)
    for v in db.session.scalars(db.select(VaultVersion).where(VaultVersion.entry_id == e.id)):
        db.session.delete(v)
    db.session.delete(e)
    audit.record("delete", "secret", before["path"], before, None)
    db.session.commit()
    return "", 204


@bp.get("/vault/refs")
def vault_refs():
    """What a plugin setting or an Ansible variable may point at: every field of the entries you
    may reveal (pointing at a secret hands it out, so it takes the same right)."""
    out = []
    for e in db.session.scalars(db.select(VaultEntry).order_by(VaultEntry.path)):
        if _can("apply", e):
            out += [{"ref": f"vault:{e.path}#{f}", "path": e.path, "field": f, "description": e.description or "", "host": e.host.name if e.host else ""}
                    for f in e.fields or []]
    return jsonify({"refs": out, **keyvault.status()})


@bp.post("/vault/entries/<uid>/reveal")
def vault_reveal(uid):
    """{field, version?}: one value, written to the history (who, what, from where; never the value)."""
    e = _find(uid)
    if e is None:
        return err(404, "no such secret")
    if not _can("apply", e):
        return err(403, "revealing needs secret:apply here")
    if _reauth_needed():
        return err(403, "confirm your password first", [{"field": "password", "message": "reauth"}])
    d = body()
    field = str(d.get("field") or "")
    try:
        values = keyvault.read(e, d.get("version"))
    except keyvault.VaultError as exc:
        return _fail(exc)
    if field not in values:
        return err(404, f"no field '{field}'")
    version = d.get("version") or e.version
    audit.record("reveal", "secret", e.path, summary=f"revealed {e.path}#{field}" + (f" (version {version})" if d.get("version") else ""))
    db.session.commit()
    return jsonify({"field": field, "value": values[field], "version": version})
