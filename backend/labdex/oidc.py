"""Sign in with OpenID Connect (Authentik, Keycloak, Authelia, Pocket ID, Zitadel …).

The authorization code flow with PKCE, configured through the environment:

    LABDEX_OIDC_ISSUER         https://auth.example.net/application/o/labdex/  (its discovery document is read)
    LABDEX_OIDC_CLIENT_ID      labdex
    LABDEX_OIDC_CLIENT_SECRET  …            (empty for a public client: PKCE alone)
    LABDEX_OIDC_NAME           Authentik    (the button: "Sign in with Authentik")
    LABDEX_OIDC_SCOPES         openid profile email groups
    LABDEX_OIDC_USERNAME_CLAIM preferred_username
    LABDEX_OIDC_GROUPS_CLAIM   groups
    LABDEX_OIDC_ADMIN_GROUPS   labdex-admins     (comma separated: members are labdex administrators)
    LABDEX_OIDC_AUTOCREATE     true              (a first sign-in creates the account)
    LABDEX_OIDC_ONLY           false             (true hides the password form; the first administrator can still set up)

The redirect URI to register at the provider is <public URL>/api/auth/oidc/callback (Settings ›
General › public URL, else the address labdex was reached at).

The ID token comes straight from the provider's token endpoint over TLS, which the spec
accepts in place of checking its signature (OIDC Core 3.1.3.7); its issuer, audience, expiry
and nonce are checked. Groups from the token (or the userinfo endpoint) become labdex group
memberships for groups that exist in labdex by that name; the account is then linked to the
provider's subject, so a renamed user stays the same account.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import requests
from flask import current_app, request

from .models import Group, User, db, utcnow

COOKIE = "labdex_oidc"
_discovery: dict = {}


class OidcError(Exception):
    pass


def config() -> dict:
    c = current_app.config
    return {k: c.get(f"LABDEX_OIDC_{k.upper()}") for k in
            ("issuer", "client_id", "client_secret", "name", "scopes", "username_claim", "groups_claim", "admin_groups", "autocreate", "only")}


def enabled() -> bool:
    c = config()
    return bool(c["issuer"] and c["client_id"])


def public_info() -> dict:
    """What the login page needs."""
    c = config()
    return {"enabled": enabled(), "name": c["name"] or "single sign-on", "only": bool(c["only"]) and enabled()}


def discovery() -> dict:
    iss = config()["issuer"].rstrip("/")
    hit = _discovery.get(iss)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        r = requests.get(f"{iss}/.well-known/openid-configuration", timeout=10)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise OidcError(f"can't read the provider's discovery document: {exc}") from exc
    if d.get("issuer", "").rstrip("/") != iss:
        raise OidcError(f"the provider says its issuer is {d.get('issuer')!r}, not {iss!r}")
    _discovery[iss] = (time.time(), d)
    return d


def redirect_uri() -> str:
    from . import naming
    base = naming.get_setting("public_url").strip().rstrip("/") or request.host_url.rstrip("/")
    return f"{base}/api/auth/oidc/callback"


def _signer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.secret_key, salt="labdex-oidc")


def start(next_path: str = "/") -> tuple[str, str]:
    """(where to send the browser, the value of the flow cookie)."""
    c = config()
    d = discovery()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    nxt = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    cookie = _signer().dumps({"state": state, "nonce": nonce, "verifier": verifier, "next": nxt})
    q = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": redirect_uri(), "scope": c["scopes"] or "openid profile email",
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{d['authorization_endpoint']}?{urlencode(q)}", cookie


def _b64json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def finish(args: dict, cookie: str) -> tuple[dict, str]:
    """The provider sent the browser back: (claims, where to go). Raises OidcError."""
    if args.get("error"):
        raise OidcError(f"the provider said: {args.get('error_description') or args['error']}")
    try:
        flow = _signer().loads(cookie or "", max_age=600)
    except Exception as exc:
        raise OidcError("the sign-in took too long or started in another browser; try again") from exc
    if not args.get("state") or not secrets.compare_digest(str(args["state"]), flow["state"]):
        raise OidcError("the answer doesn't belong to this sign-in (state); try again")
    c, d = config(), discovery()
    data = {"grant_type": "authorization_code", "code": args.get("code", ""), "redirect_uri": redirect_uri(), "code_verifier": flow["verifier"],
            "client_id": c["client_id"]}
    auth = (c["client_id"], c["client_secret"]) if c["client_secret"] else None
    try:
        r = requests.post(d["token_endpoint"], data=data, auth=auth, timeout=10, headers={"Accept": "application/json"})
        tok = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise OidcError(f"the provider's token endpoint didn't answer: {exc}") from exc
    if r.status_code != 200 or "id_token" not in tok:
        raise OidcError(f"the provider refused the code: {tok.get('error_description') or tok.get('error') or r.status_code}")
    try:
        claims = _b64json(tok["id_token"].split(".")[1])
    except (ValueError, IndexError) as exc:
        raise OidcError("the ID token isn't readable") from exc
    aud = claims.get("aud")
    if claims.get("iss", "").rstrip("/") != d["issuer"].rstrip("/"):
        raise OidcError("the ID token is from another issuer")
    if c["client_id"] not in (aud if isinstance(aud, list) else [aud]):
        raise OidcError("the ID token is for another client")
    if int(claims.get("exp", 0)) < time.time() - 60:
        raise OidcError("the ID token has expired")
    if claims.get("nonce") != flow["nonce"]:
        raise OidcError("the ID token doesn't belong to this sign-in (nonce)")
    gclaim = c["groups_claim"] or "groups"
    if (gclaim not in claims or not claims.get(c["username_claim"] or "preferred_username")) and d.get("userinfo_endpoint") and tok.get("access_token"):
        try:
            ui = requests.get(d["userinfo_endpoint"], headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=10).json()
            if ui.get("sub") == claims.get("sub"):
                claims = {**ui, **claims}
        except (requests.RequestException, ValueError):
            pass
    return claims, flow["next"]


def user_for(claims: dict) -> User:
    """The labdex account for these claims: linked by subject, else by user name (an account
    not linked to another subject yet), else a new one. Raises OidcError."""
    c = config()
    subject = f"{claims.get('iss', '').rstrip('/')}|{claims.get('sub', '')}"
    if not claims.get("sub"):
        raise OidcError("the provider didn't say who you are (no sub)")
    raw = str(claims.get(c["username_claim"] or "preferred_username") or claims.get("email") or claims["sub"])
    username = "".join(ch for ch in raw.split("@")[0].lower() if ch.isalnum() or ch in ".-_")[:64] or claims["sub"][:64].lower()
    user = db.session.scalar(db.select(User).where(User.oidc_subject == subject))
    if user is None:
        user = db.session.scalar(db.select(User).where(User.username == username, User.oidc_subject.is_(None)))
        if user is None:
            if not c["autocreate"]:
                raise OidcError(f"there's no labdex account for {username}; ask an administrator to create it")
            if db.session.scalar(db.select(User).where(User.username == username)):
                raise OidcError(f"the account {username} belongs to someone else's sign-in")
            user = User(username=username, display_name="", password_hash="", is_admin=False, active=True)
            db.session.add(user)
        user.oidc_subject = subject
    if not user.active:
        raise OidcError("this account is disabled")
    user.display_name = str(claims.get("name") or user.display_name or username)[:128]
    groups = claims.get(c["groups_claim"] or "groups")
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.replace(";", ",").split(",") if g.strip()]
    if isinstance(groups, list):
        names = {str(g).rsplit("/", 1)[-1] for g in groups}  # Keycloak sends /parent/child
        user.groups = list(db.session.scalars(db.select(Group).where(Group.name.in_(names)))) if names else []
        admins = {g.strip() for g in str(c["admin_groups"] or "").split(",") if g.strip()}
        if admins:
            user.is_admin = bool(admins & names)
    user.last_login = utcnow()
    return user
