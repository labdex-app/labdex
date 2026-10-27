"""Who is calling: session cookie, API token, or a trusted SSO header.

- Session: username/password login; cookie is HttpOnly, SameSite=Strict.
- Token: `Authorization: Bearer ldx_…`; stored hashed, optionally read-only or expiring.
- SSO: a forward-auth proxy (e.g. Authentik via Traefik) sets LABDEX_SSO_HEADER;
  it's only trusted when the request comes straight from LABDEX_TRUSTED_PROXIES.

State-changing requests authenticated by cookie or SSO must carry
`X-Requested-With: labdex`, which browsers can't send cross-site without a
CORS preflight that labdex never allows.
"""
from __future__ import annotations

import ipaddress
import time
from collections import defaultdict
from datetime import timedelta

from flask import current_app, g, request, session

from . import security
from .models import ApiToken, Group, User, db, utcnow
from .rbac import Principal

CSRF_HEADER = ("X-Requested-With", "labdex")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# ------------------------------------------------------------- brute force
_failures: dict[str, list[float]] = defaultdict(list)
WINDOW, LIMIT = 300, 5


USER_LIMIT = 20  # failures for one username from anywhere, per WINDOW


def throttled(key: str, limit: int = LIMIT) -> int:
    """Seconds to wait before another attempt (0 = allowed)."""
    now = time.monotonic()
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    if len(recent) < limit:
        return 0
    return int(WINDOW - (now - recent[-limit])) + 1


def record_failure(key: str) -> None:
    if len(_failures) > 10000:  # random usernames must not grow this forever
        now = time.monotonic()
        for k in [k for k, v in _failures.items() if not v or now - v[-1] >= WINDOW]:
            _failures.pop(k, None)
    _failures[key].append(time.monotonic())


def client_ip() -> str:
    """The caller's address. Behind a trusted proxy: the last address in X-Forwarded-For that
    isn't one of the proxies (so one client's failures don't lock out everyone behind it)."""
    addr = request.remote_addr or ""
    if not _trusted_proxy():
        return addr
    nets = [ipaddress.ip_network(n, strict=False) for n in current_app.config.get("LABDEX_TRUSTED_PROXIES") or []]
    for hop in reversed([h.strip() for h in request.headers.get("X-Forwarded-For", "").split(",") if h.strip()]):
        try:
            ip = ipaddress.ip_address(hop)
        except ValueError:
            break
        if not any(ip in n for n in nets):
            return str(ip)
    return addr


def clear_failures(key: str) -> None:
    _failures.pop(key, None)


# ------------------------------------------------------------- resolution
def _trusted_proxy() -> bool:
    nets = current_app.config.get("LABDEX_TRUSTED_PROXIES") or []
    try:
        addr = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(n, strict=False) for n in nets)


def _sso_principal() -> Principal | None:
    header = current_app.config.get("LABDEX_SSO_HEADER")
    if not header or not request.headers.get(header) or not _trusted_proxy():
        return None
    username = request.headers[header].strip().lower()[:64]
    user = db.session.scalar(db.select(User).where(User.username == username))
    if user is None:
        if not current_app.config.get("LABDEX_SSO_AUTOCREATE"):
            return None
        user = User(username=username, display_name=username)
        db.session.add(user)
    gheader = current_app.config.get("LABDEX_SSO_GROUPS_HEADER")
    if gheader and request.headers.get(gheader):
        names = {n.strip() for n in request.headers[gheader].replace(";", ",").split(",") if n.strip()}
        user.groups = list(db.session.scalars(db.select(Group).where(Group.name.in_(names))))
    db.session.commit()
    return Principal(user, via="sso") if user.active else None


def _token_principal() -> Principal | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    full = auth[7:].strip()
    prefix = security.split_token(full)
    if prefix is None:
        return None
    tok = db.session.scalar(db.select(ApiToken).where(ApiToken.prefix == prefix))
    if tok is None or not security.same(tok.token_hash, security.hash_token(full)):
        return None
    if tok.expires_at and tok.expires_at < utcnow():
        return None
    if not tok.user.active:
        return None
    now = utcnow()
    if tok.last_used_at is None or now - tok.last_used_at > timedelta(minutes=5):
        tok.last_used_at = now
        db.session.commit()
    p = Principal(tok.user, via="token", read_only=tok.read_only, label=f"{tok.user.username} (token {tok.name})")
    p.token_id = tok.id
    return p


def _session_principal() -> Principal | None:
    uid = session.get("uid")
    if not uid:
        return None
    user = db.session.get(User, uid)
    if user is None or not user.active or session.get("pwv") != _password_version(user):
        session.clear()
        return None
    p = Principal(user, via="session")
    p.session_pwv = session.get("pwv") or ""
    return p


def _password_version(user: User) -> str:
    """Changes when the password changes, so old sessions end."""
    return security.hash_token(user.password_hash or user.username)[:12]


def resolve() -> Principal:
    for fn in (_token_principal, _sso_principal, _session_principal):
        p = fn()
        if p is not None:
            return p
    return Principal(None)


def load_principal() -> None:
    g.principal = resolve()


def csrf_ok() -> bool:
    if request.method in SAFE_METHODS:
        return True
    p = g.get("principal")
    if p is not None and p.via == "token":
        return True
    return request.headers.get(CSRF_HEADER[0]) == CSRF_HEADER[1]


# ---------------------------------------------------------------- sessions
def login_session(user: User) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = user.id
    session["pwv"] = _password_version(user)
    user.last_login = utcnow()


def logout_session() -> None:
    session.clear()


def user_count() -> int:
    return db.session.scalar(db.select(db.func.count()).select_from(User))
