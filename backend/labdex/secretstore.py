"""Secret references: what plugins and Ansible variables point at instead of holding a value.

Since 1.18 every secret lives in the vault (keyvault.py). A reference names an entry and a field:

    vault:hosts/srv-pm-pve-01/root#password     a field of an entry
    vault:wifi/iot                              an entry with one field (or a field named value)
    grafana-admin                               the older "global secret": vault:global/grafana-admin#value

Plugins store {"$ref": "<reference>"} in a secret setting; Ansible variables hold
{$secret: "<reference>"}. Values are read only while a job runs, from an unsealed vault.

The global secrets of 1.17 and before (the Secret table, encrypted with secret.key) move into
the vault as global/<name> the first time an administrator unseals it (migrate_legacy); until
then they still resolve from the old table.
"""
from __future__ import annotations

import re

from flask import current_app, has_request_context

from .models import Secret, VaultEntry, db

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")  # an older global secret's name
GLOBAL = "global/"


class RefError(Exception):
    """A reference that can't be used: unknown, sealed, or not allowed."""


# ---------------------------------------------------------------------------- parsing
def target(ref: str) -> tuple[str, str]:
    """(path, field) a reference points at; field '' = the entry's only field."""
    ref = str(ref or "").strip()
    if ref.startswith("vault:"):
        path, _, field = ref[6:].partition("#")
        return path.strip().strip("/").lower(), field.strip()
    return GLOBAL + ref.lower(), "value"


def label(ref: str) -> str:
    path, field = target(ref)
    return f"{path}#{field}" if field else path


def entry(ref: str) -> VaultEntry | None:
    return db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == target(ref)[0]))


def _field(e: VaultEntry, field: str) -> str | None:
    names = list(e.fields or [])
    if field:
        return field if field in names else None
    return names[0] if len(names) == 1 else ("value" if "value" in names else None)


def _legacy(ref: str) -> Secret | None:
    if str(ref).startswith("vault:"):
        return None
    return db.session.scalar(db.select(Secret).where(Secret.name == str(ref).lower()))


def exists(ref: str) -> bool:
    """Whether the reference points at something (no unsealing needed: field names are plain)."""
    e = entry(ref)
    if e is not None:
        return _field(e, target(ref)[1]) is not None
    return _legacy(ref) is not None


def reveal(ref: str) -> str | None:
    """The value, for a job that runs now. Never send it to a client. None: no such secret.
    Raises RefError when the vault is sealed."""
    from . import keyvault
    e = entry(ref)
    if e is not None:
        field = _field(e, target(ref)[1])
        if field is None:
            return None
        try:
            return keyvault.read(e)[field]
        except keyvault.Sealed:
            raise RefError(f"the vault is sealed, so {label(ref)} can't be read: an administrator unseals it") from None
    old = _legacy(ref)
    return None if old is None else current_app.extensions["labdex"]["vault"].decrypt(old.value)


# ----------------------------------------------------------------------------- rights
def may_reference(ref: str, principal=None) -> bool:
    """Pointing something at a secret makes labdex hand it out (to a plugin, to Ansible): only
    for those who may reveal it themselves (secret:apply on the entry)."""
    if principal is None:
        if not has_request_context():
            return True  # labdex itself (an import from the command line, a migration)
        from flask import g
        principal = getattr(g, "principal", None)
        if principal is None:
            return True
    from . import keyvault
    e = entry(ref)
    if e is not None:
        return principal.can("secret", "apply", keyvault.facts(e))
    return bool(principal.is_admin)  # an old global secret not moved yet


def check_new(old_refs, new_refs, field: str, must_exist: bool = True) -> list[dict]:
    """Errors for references that are new in this save: unknown ones (unless they may come later,
    like in Ansible variables, where lint points them out), and ones the caller may not hand out.
    References someone else saved before stay when others edit."""
    errors = []
    for ref in sorted(set(new_refs) - set(old_refs)):
        if not exists(ref):
            if must_exist:
                errors.append({"field": field, "message": f"no secret {label(ref)} in the vault"})
        elif not may_reference(ref):
            errors.append({"field": field, "message": f"pointing at {label(ref)} needs secret:apply on it"})
    return errors


# ------------------------------------------------------------------- who uses what
def _refs_in_settings(settings: dict) -> set[str]:
    return {v["$ref"] for v in (settings or {}).values() if isinstance(v, dict) and isinstance(v.get("$ref"), str)}


def all_refs() -> list[tuple[str, str]]:
    """(reference, who) for every reference labdex holds."""
    from . import ansiblevault
    from .models import Feature, Host, HostModule, Integration
    out = []
    for i in db.session.scalars(db.select(Integration)):
        out += [(r, i.name) for r in _refs_in_settings(i.settings)]
    for hm in db.session.scalars(db.select(HostModule)):
        out += [(r, f"{hm.integration.name}@{hm.host_name}") for r in _refs_in_settings(hm.settings)]
    for f in db.session.scalars(db.select(Feature)):
        out += [(r, f"feature {f.path}") for r in ansiblevault.refs(f.vars or {})]
    for h in db.session.scalars(db.select(Host).where(Host.vars.is_not(None))):
        out += [(r, f"host {h.name}") for r in ansiblevault.refs(h.vars or {})]
    return out


def used_by(path: str) -> list[str]:
    """Who points at the vault entry at this path."""
    return sorted({who for ref, who in all_refs() if target(ref)[0] == path})


def rename(old_path: str, new_path: str) -> int:
    """An entry moved: every reference to it follows (written as vault:<new>#<field>)."""
    from . import ansiblevault
    from .models import Feature, Host, HostModule, Integration

    def moved(ref):
        path, field = target(ref)
        return f"vault:{new_path}#{field}" if field else f"vault:{new_path}"

    n = 0

    def fix_settings(obj):
        nonlocal n
        if not obj.settings:
            return
        changed = {}
        for k, v in obj.settings.items():
            if isinstance(v, dict) and isinstance(v.get("$ref"), str) and target(v["$ref"])[0] == old_path:
                changed[k] = {"$ref": moved(v["$ref"])}
        if changed:
            obj.settings = {**obj.settings, **changed}
            n += len(changed)

    def fix_vars(obj):
        nonlocal n
        if obj.vars and any(target(r)[0] == old_path for r in ansiblevault.refs(obj.vars)):
            def swap(ref):
                nonlocal n
                if target(ref)[0] != old_path:
                    return {ansiblevault.REF: ref}
                n += 1
                return {ansiblevault.REF: moved(ref)}
            obj.vars = ansiblevault.replace(obj.vars, swap)

    for i in db.session.scalars(db.select(Integration)):
        fix_settings(i)
    for hm in db.session.scalars(db.select(HostModule)):
        fix_settings(hm)
    for f in db.session.scalars(db.select(Feature)):
        fix_vars(f)
    for h in db.session.scalars(db.select(Host).where(Host.vars.is_not(None))):
        fix_vars(h)
    return n


# ----------------------------------------------------------- the older global secrets
def migrate_legacy(actor: str) -> list[str]:
    """Moves the global secrets of 1.17 and before into the (unsealed) vault as global/<name>,
    field value. References by bare name keep working: they mean exactly that entry."""
    from . import keyvault
    moved = []
    fernet = current_app.extensions["labdex"]["vault"]
    for s in db.session.scalars(db.select(Secret).order_by(Secret.name)):
        path = GLOBAL + s.name
        if db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == path)) is None:
            keyvault.create(path, {"value": fernet.decrypt(s.value)}, actor, description=s.description or "")
        db.session.delete(s)
        moved.append(s.name)
    if moved:
        db.session.flush()
    return moved


def legacy_count() -> int:
    return len(list(db.session.scalars(db.select(Secret.id))))


# ------------------------------------------------- /api/secrets: global/ entries by name
def put(name: str, value: str, description: str | None, actor: str) -> VaultEntry:
    from . import keyvault
    e = entry(name)
    if e is None:
        return keyvault.create(GLOBAL + name, {"value": value}, actor, description=description or "")
    keyvault.update(e, actor, fields={"value": value} if value else None, description=description)
    return e


def to_dict(e: VaultEntry) -> dict:
    return {"name": e.path[len(GLOBAL):], "ref": f"vault:{e.path}#value", "description": e.description or "",
            "updated_at": e.updated_at.isoformat() + "Z" if e.updated_at else None, "updated_by": e.updated_by or "", "used_by": used_by(e.path)}
