"""Global secrets by name, as before 1.18: since then they're vault entries under global/ (field
value), so this is a thin layer over the vault for scripts and plugins that use the old API.
Reading values goes through the vault (reveal); writing needs it unsealed."""
from __future__ import annotations

from flask import g, jsonify

from .. import audit, keyvault, rbac, store
from .. import secretstore as secrets
from ..models import VaultEntry, db
from . import body, bp, err


@bp.get("/secrets")
def list_secrets():
    if not g.principal.can_any("integration", "admin"):
        return err(403, "you lack integration:admin")
    entries = db.session.scalars(db.select(VaultEntry).where(VaultEntry.path.like(secrets.GLOBAL + "%")).order_by(VaultEntry.path))
    return jsonify([secrets.to_dict(e) for e in entries])


@bp.put("/secrets/<name>")
def put_secret(name):
    rbac.require_admin()
    data = body()
    errors = []
    if not secrets.NAME_RE.match(name):
        errors.append({"field": "name", "message": "lowercase letters, digits, '.', '-' and '_'"})
    value = data.get("value")
    exists = secrets.entry(name) is not None
    if not exists and not value:
        errors.append({"field": "value", "message": "required"})
    if value is not None and not isinstance(value, str):
        errors.append({"field": "value", "message": "text"})
    if value and len(value) > 16000:
        errors.append({"field": "value", "message": "too long (16000 characters at most)"})
    if errors:
        raise store.ValidationError(errors)
    try:
        e = secrets.put(name, value or "", data.get("description"), g.principal.label)
    except keyvault.VaultError as exc:
        db.session.rollback()
        return err(exc.status, str(exc))
    db.session.flush()
    audit.record("update" if exists else "create", "secret", e.path,
                 summary=("value replaced" if value else "description changed") if exists else "created")
    db.session.commit()
    return jsonify(secrets.to_dict(e)), 200 if exists else 201


@bp.delete("/secrets/<name>")
def delete_secret(name):
    rbac.require_admin()
    e = secrets.entry(name)
    if e is None:
        return err(404, "no such secret")
    users = secrets.used_by(e.path)
    if users:
        return err(409, f"still used by {', '.join(users)}")
    from ..models import VaultVersion
    for v in db.session.scalars(db.select(VaultVersion).where(VaultVersion.entry_id == e.id)):
        db.session.delete(v)
    audit.record("delete", "secret", e.path, summary="deleted")
    db.session.delete(e)
    db.session.commit()
    return "", 204
