"""Administration: users, groups, roles, role bindings, object grants, settings, backup."""
from __future__ import annotations

from flask import g, jsonify

from .. import audit, inventory, naming, rbac, security, store
from ..models import Group, ObjectGrant, Role, RoleBinding, User, db
from ..plugins import jobs
from . import body, bp, err, text_response
from .auth_routes import _new_user


@bp.before_request
def _admin_only():
    from flask import request
    if request.path.startswith("/api/admin/"):
        rbac.require_admin()


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "is_admin": u.is_admin,
            "active": u.active, "groups": [gr.name for gr in u.groups], "has_password": bool(u.password_hash),
            "created_at": u.created_at.isoformat() + "Z",
            "last_login": u.last_login.isoformat() + "Z" if u.last_login else None,
            "tokens": len(u.tokens)}


def _snapshot_user(u: User) -> dict:
    d = user_dict(u)
    for k in ("created_at", "last_login", "tokens", "has_password", "id"):
        d.pop(k)
    return d


def _groups_by_name(names) -> list[Group]:
    names = [str(n) for n in names or []]
    found = list(db.session.scalars(db.select(Group).where(Group.name.in_(names))))
    missing = set(names) - {gr.name for gr in found}
    if missing:
        raise store.ValidationError([{"field": "groups", "message": f"unknown group(s): {', '.join(sorted(missing))}"}])
    return found


# ---------------------------------------------------------------------- users
@bp.get("/admin/users")
def admin_users():
    return jsonify([user_dict(u) for u in db.session.scalars(db.select(User).order_by(User.username))])


@bp.post("/admin/users")
def admin_create_user():
    data = body()
    user = _new_user(data, admin=bool(data.get("is_admin")))
    user.groups = _groups_by_name(data.get("groups"))
    db.session.flush()
    audit.record("create", "user", user.username, None, _snapshot_user(user))
    db.session.commit()
    return jsonify(user_dict(user)), 201


@bp.put("/admin/users/<int:uid>")
def admin_update_user(uid):
    user = db.session.get(User, uid)
    if user is None:
        return err(404, "no such user")
    data = body()
    before = _snapshot_user(user)
    if "display_name" in data:
        user.display_name = str(data["display_name"])[:128]
    if "is_admin" in data or "active" in data:
        if user.id == g.principal.user.id and (data.get("is_admin") is False or data.get("active") is False):
            return err(409, "you can't remove your own admin rights or deactivate yourself")
        user.is_admin = bool(data.get("is_admin", user.is_admin))
        user.active = bool(data.get("active", user.active))
    if "groups" in data:
        user.groups = _groups_by_name(data["groups"])
    if data.get("password"):
        if len(str(data["password"])) < security.MIN_PASSWORD:
            raise store.ValidationError([{"field": "password", "message": f"at least {security.MIN_PASSWORD} characters"}])
        user.password_hash = security.hash_password(str(data["password"]))
    after = _snapshot_user(user)
    audit.record("update", "user", user.username, before, after,
                 summary=None if not data.get("password") else "password reset by an administrator")
    db.session.commit()
    return jsonify(user_dict(user))


@bp.delete("/admin/users/<int:uid>")
def admin_delete_user(uid):
    user = db.session.get(User, uid)
    if user is None:
        return err(404, "no such user")
    if user.id == g.principal.user.id:
        return err(409, "you can't delete yourself")
    audit.record("delete", "user", user.username, _snapshot_user(user), None)
    db.session.delete(user)
    db.session.commit()
    return "", 204


# --------------------------------------------------------------------- groups
def group_dict(gr: Group) -> dict:
    return {"id": gr.id, "name": gr.name, "description": gr.description, "members": sorted(u.username for u in gr.members)}


@bp.get("/admin/groups")
def admin_groups():
    return jsonify([group_dict(gr) for gr in db.session.scalars(db.select(Group).order_by(Group.name))])


@bp.post("/admin/groups")
@bp.put("/admin/groups/<int:gid>")
def admin_save_group(gid=None):
    data = body()
    gr = db.session.get(Group, gid) if gid else Group()
    if gr is None:
        return err(404, "no such group")
    before = group_dict(gr) if gid else None
    name = str(data.get("name", "")).strip()
    clash = db.session.scalar(db.select(Group).where(Group.name == name))
    if not name or (clash is not None and clash is not gr):
        raise store.ValidationError([{"field": "name", "message": "required and unique"}])
    gr.name, gr.description = name, str(data.get("description", ""))
    usernames = [str(u) for u in data.get("members") or []]
    gr.members = list(db.session.scalars(db.select(User).where(User.username.in_(usernames))))
    db.session.add(gr)
    db.session.flush()
    audit.record("update" if gid else "create", "group", gr.name, before, group_dict(gr))
    db.session.commit()
    return jsonify(group_dict(gr)), 200 if gid else 201


@bp.delete("/admin/groups/<int:gid>")
def admin_delete_group(gid):
    gr = db.session.get(Group, gid)
    if gr is None:
        return err(404, "no such group")
    audit.record("delete", "group", gr.name, group_dict(gr), None)
    db.session.delete(gr)
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------- roles
def role_dict(r: Role) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": r.permissions, "builtin": r.builtin}


@bp.get("/admin/roles")
def admin_roles():
    return jsonify({"roles": [role_dict(r) for r in db.session.scalars(db.select(Role).order_by(Role.name))],
                    "object_types": rbac.OBJECT_TYPES, "actions": rbac.ACTIONS})


@bp.post("/admin/roles")
@bp.put("/admin/roles/<int:rid>")
def admin_save_role(rid=None):
    data = body()
    role = db.session.get(Role, rid) if rid else Role()
    if role is None:
        return err(404, "no such role")
    if role.builtin:
        return err(409, "built-in roles can't be changed; create your own")
    perms = [str(p) for p in data.get("permissions") or []]
    bad = rbac.validate_permissions(perms)
    name = str(data.get("name", "")).strip()
    clash = db.session.scalar(db.select(Role).where(Role.name == name))
    errors = []
    if bad:
        errors.append({"field": "permissions", "message": f"invalid: {', '.join(bad)}"})
    if not name or (clash is not None and clash is not role):
        errors.append({"field": "name", "message": "required and unique"})
    if errors:
        raise store.ValidationError(errors)
    before = role_dict(role) if rid else None
    role.name, role.description, role.permissions = name, str(data.get("description", "")), perms
    db.session.add(role)
    db.session.flush()
    audit.record("update" if rid else "create", "role", role.name, before, role_dict(role))
    db.session.commit()
    return jsonify(role_dict(role)), 200 if rid else 201


@bp.delete("/admin/roles/<int:rid>")
def admin_delete_role(rid):
    role = db.session.get(Role, rid)
    if role is None:
        return err(404, "no such role")
    if role.builtin:
        return err(409, "built-in roles can't be deleted")
    audit.record("delete", "role", role.name, role_dict(role), None)
    db.session.delete(role)
    db.session.commit()
    return "", 204


# ------------------------------------------------------------ bindings/grants
def _subject(data) -> tuple[User | None, Group | None]:
    user = group = None
    if data.get("user"):
        user = db.session.scalar(db.select(User).where(User.username == str(data["user"])))
        if user is None:
            raise store.ValidationError([{"field": "user", "message": "unknown user"}])
    elif data.get("group"):
        group = db.session.scalar(db.select(Group).where(Group.name == str(data["group"])))
        if group is None:
            raise store.ValidationError([{"field": "group", "message": "unknown group"}])
    else:
        raise store.ValidationError([{"field": "user", "message": "give a user or a group"}])
    return user, group


def binding_dict(b: RoleBinding) -> dict:
    return {"id": b.id, "role": b.role.name, "user": b.user.username if b.user else None,
            "group": b.group.name if b.group else None, "scopes": b.scopes or ["*"]}


@bp.get("/admin/bindings")
def admin_bindings():
    return jsonify([binding_dict(b) for b in db.session.scalars(db.select(RoleBinding).order_by(RoleBinding.id))])


@bp.post("/admin/bindings")
def admin_create_binding():
    data = body()
    user, group = _subject(data)
    role = db.session.scalar(db.select(Role).where(Role.name == str(data.get("role", ""))))
    if role is None:
        raise store.ValidationError([{"field": "role", "message": "unknown role"}])
    scopes = [str(s).strip() for s in data.get("scopes") or ["*"] if str(s).strip()] or ["*"]
    bad = rbac.validate_scopes(scopes)
    if bad:
        raise store.ValidationError([{"field": "scopes", "message": f"invalid: {', '.join(bad)} (use *, zone:, area:, network:, tag:, name:)"}])
    b = RoleBinding(role=role, user=user, group=group, scopes=scopes)
    db.session.add(b)
    db.session.flush()
    audit.record("create", "binding", f"{role.name}→{(user or group).username if user else group.name}", None, binding_dict(b))
    db.session.commit()
    return jsonify(binding_dict(b)), 201


@bp.delete("/admin/bindings/<int:bid>")
def admin_delete_binding(bid):
    b = db.session.get(RoleBinding, bid)
    if b is None:
        return err(404, "no such binding")
    d = binding_dict(b)
    audit.record("delete", "binding", f"{d['role']}→{d['user'] or d['group']}", d, None)
    db.session.delete(b)
    db.session.commit()
    return "", 204


def grant_dict(gr: ObjectGrant) -> dict:
    return {"id": gr.id, "user": gr.user.username if gr.user else None, "group": gr.group.name if gr.group else None,
            "object_type": gr.object_type, "object_name": gr.object_name, "actions": gr.actions}


@bp.get("/admin/grants")
def admin_grants():
    return jsonify([grant_dict(x) for x in db.session.scalars(db.select(ObjectGrant).order_by(ObjectGrant.id))])


@bp.post("/admin/grants")
def admin_create_grant():
    data = body()
    user, group = _subject(data)
    otype = str(data.get("object_type", ""))
    actions = [str(a) for a in data.get("actions") or []]
    errors = []
    if otype not in rbac.OBJECT_TYPES and otype != "*":
        errors.append({"field": "object_type", "message": f"one of {', '.join(rbac.OBJECT_TYPES)}"})
    if not str(data.get("object_name", "")).strip():
        errors.append({"field": "object_name", "message": "required (globs like srv-* work)"})
    bad = [a for a in actions if a not in (*rbac.ACTIONS, "*")]
    if not actions or bad:
        errors.append({"field": "actions", "message": f"choose from {', '.join(rbac.ACTIONS)}"})
    if errors:
        raise store.ValidationError(errors)
    gr = ObjectGrant(user=user, group=group, object_type=otype, object_name=str(data["object_name"]).strip(), actions=actions)
    db.session.add(gr)
    db.session.flush()
    audit.record("create", "grant", f"{otype}:{gr.object_name}", None, grant_dict(gr))
    db.session.commit()
    return jsonify(grant_dict(gr)), 201


@bp.delete("/admin/grants/<int:gid>")
def admin_delete_grant(gid):
    gr = db.session.get(ObjectGrant, gid)
    if gr is None:
        return err(404, "no such grant")
    audit.record("delete", "grant", f"{gr.object_type}:{gr.object_name}", grant_dict(gr), None)
    db.session.delete(gr)
    db.session.commit()
    return "", 204


# ------------------------------------------------------------ settings/backup
@bp.put("/admin/settings")
def admin_settings():
    data = body()
    unknown = set(data) - set(naming.DEFAULT_SETTINGS)
    if unknown:
        raise store.ValidationError([{"field": k, "message": "unknown setting"} for k in sorted(unknown)])
    url = str(data.get("public_url", "")).strip().rstrip("/")
    if url and not url.startswith(("https://", "http://")):
        raise store.ValidationError([{"field": "public_url", "message": "starts with https://"}])
    if "public_url" in data:
        data["public_url"] = url
    if "alias_ids" in data and data["alias_ids"] not in ("uid", "readable"):
        raise store.ValidationError([{"field": "alias_ids", "message": "uid or readable"}])
    before = naming.settings_dict()
    store.save_settings(data)
    db.session.flush()
    audit.record("update", "setting", "conventions", before, naming.settings_dict())
    db.session.commit()
    jobs.emit("setting.updated")
    return jsonify(naming.settings_dict())


@bp.get("/admin/backup")
def admin_backup():
    return text_response(inventory.export_yaml(), "application/yaml", "inventory.yaml")


@bp.post("/admin/restore")
def admin_restore():
    data = body()
    if data.get("confirm") != "replace":
        return err(400, "restoring replaces the whole inventory; send confirm: replace")
    counts = inventory.import_yaml(str(data.get("yaml", "")), commit=False)
    audit.record("import", "inventory", "restore", summary=f"restored {counts['networks']} networks, {counts['hosts']} hosts, {counts['aliases']} aliases")
    db.session.commit()
    jobs.emit("inventory.imported")
    return jsonify({"imported": counts})
