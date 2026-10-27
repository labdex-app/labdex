from __future__ import annotations

from datetime import timedelta

from flask import current_app, g, jsonify

from .. import audit, auth, demo, rbac, security, store
from ..models import ApiToken, User, db, utcnow
from . import body, bp, err


def me_dict(p: rbac.Principal) -> dict:
    u = p.user
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name or u.username,
        "is_admin": p.is_admin, "via": p.via, "read_only": p.read_only,
        "groups": [gr.name for gr in u.groups], "permissions": p.summary(),
        "has_password": bool(u.password_hash),
    }


@bp.get("/auth/status", endpoint="auth_status")
def auth_status():
    p = g.principal
    return jsonify({
        "setup_required": auth.user_count() == 0,
        "user": me_dict(p) if p.user else None,
        "sso": bool(current_app.config.get("LABDEX_SSO_HEADER")),
        "demo": demo.info(),
    })


@bp.post("/auth/setup", endpoint="auth_setup")
def auth_setup():
    if auth.user_count() > 0:
        return err(409, "setup is already done")
    data = body()
    user = _new_user(data, admin=True)
    db.session.flush()
    audit.record("create", "user", user.username, None, {"username": user.username, "is_admin": True},
                 summary="first administrator (setup)", actor=(user.username, "user"))
    auth.login_session(user)
    db.session.commit()
    return jsonify(me_dict(rbac.Principal(user, via="session"))), 201


def _new_user(data: dict, admin=False) -> User:
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    errors = []
    if not username or not username.replace("-", "").replace("_", "").replace(".", "").isalnum() or len(username) > 64:
        errors.append({"field": "username", "message": "letters, digits, '.', '-' and '_'"})
    elif db.session.scalar(db.select(User).where(User.username == username)):
        errors.append({"field": "username", "message": "already taken"})
    if len(password) < security.MIN_PASSWORD:
        errors.append({"field": "password", "message": f"at least {security.MIN_PASSWORD} characters"})
    if errors:
        raise store.ValidationError(errors)
    user = User(username=username, display_name=str(data.get("display_name") or username)[:128],
                password_hash=security.hash_password(password), is_admin=admin)
    db.session.add(user)
    return user


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(security.hash_password("not-a-real-password"))
    return _DUMMY[0]


@bp.post("/auth/login", endpoint="auth_login")
def auth_login():
    data = body()
    username = str(data.get("username", "")).strip().lower()
    key, ukey = f"{auth.client_ip()}|{username}", f"*|{username}"
    wait = max(auth.throttled(key), auth.throttled(ukey, auth.USER_LIMIT))
    if wait:
        return err(429, f"too many attempts; try again in {wait} s")
    user = db.session.scalar(db.select(User).where(User.username == username))
    password = str(data.get("password", ""))
    if user is None:
        security.verify_password(_dummy_hash(), password)  # same cost as a real check
    if user is None or not user.active or not security.verify_password(user.password_hash, password):
        auth.record_failure(key)
        auth.record_failure(ukey)
        return err(401, "wrong username or password")
    auth.clear_failures(key)
    auth.login_session(user)
    audit.record("login", "user", user.username, summary="logged in", actor=(user.username, "user"))
    db.session.commit()
    return jsonify(me_dict(rbac.Principal(user, via="session")))


@bp.post("/auth/logout")
def auth_logout():
    auth.logout_session()
    return "", 204


@bp.get("/auth/me")
def auth_me():
    return jsonify(me_dict(g.principal))


@bp.post("/auth/password")
def auth_password():
    p = g.principal
    if p.via != "session":
        return err(403, "change your password in the browser session")
    data = body()
    if not security.verify_password(p.user.password_hash, str(data.get("current", ""))):
        return err(422, "validation failed", [{"field": "current", "message": "wrong password"}])
    new = str(data.get("new", ""))
    if len(new) < security.MIN_PASSWORD:
        return err(422, "validation failed", [{"field": "new", "message": f"at least {security.MIN_PASSWORD} characters"}])
    p.user.password_hash = security.hash_password(new)
    audit.record("update", "user", p.user.username, summary="changed own password")
    auth.login_session(p.user)
    db.session.commit()
    return "", 204


# -------------------------------------------------------------------- tokens
def token_dict(t: ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": f"ldx_{t.prefix}_…", "read_only": t.read_only,
            "expires_at": t.expires_at.isoformat() + "Z" if t.expires_at else None,
            "created_at": t.created_at.isoformat() + "Z",
            "last_used_at": t.last_used_at.isoformat() + "Z" if t.last_used_at else None}


@bp.get("/auth/tokens")
def list_tokens():
    return jsonify([token_dict(t) for t in g.principal.user.tokens])


@bp.post("/auth/tokens")
def create_token():
    p = g.principal
    if p.via == "token":
        return err(403, "tokens can't create tokens")
    data = body()
    name = str(data.get("name", "")).strip()[:64]
    if not name:
        return err(422, "validation failed", [{"field": "name", "message": "required"}])
    days = data.get("expires_days")
    full, prefix, hashed = security.new_token()
    tok = ApiToken(user=p.user, name=name, prefix=prefix, token_hash=hashed, read_only=bool(data.get("read_only")),
                   expires_at=utcnow() + timedelta(days=int(days)) if days else None)
    db.session.add(tok)
    audit.record("create", "token", f"{p.user.username}/{name}", summary=f"API token {'(read-only) ' if tok.read_only else ''}created")
    db.session.commit()
    return jsonify({**token_dict(tok), "token": full}), 201


@bp.delete("/auth/tokens/<int:token_id>")
def delete_token(token_id):
    tok = db.session.get(ApiToken, token_id)
    if tok is None or (tok.user_id != g.principal.user.id and not g.principal.is_admin):
        return err(404, "no such token")
    audit.record("delete", "token", f"{tok.user.username}/{tok.name}", summary="API token revoked")
    db.session.delete(tok)
    db.session.commit()
    return "", 204
