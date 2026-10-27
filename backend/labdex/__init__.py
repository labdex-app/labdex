"""labdex: a small homelab inventory with zones, naming rules, RBAC, history and plugins."""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify

from . import httpcache
from sqlalchemy import event

from .models import db

__version__ = "1.24.0"

STATIC_DIR = Path(__file__).parent / "static"


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    data_dir = Path(os.environ.get("LABDEX_DATA", "./data")).resolve()
    testing = bool(config and config.get("TESTING"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get("LABDEX_DB", f"sqlite:///{data_dir / 'labdex.db'}"),
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 15}},
        LABDEX_VERSION=__version__,
        LABDEX_DATA_DIR=str(data_dir),
        LABDEX_PLUGIN_DIR=os.environ.get("LABDEX_PLUGIN_DIR", str(data_dir / "plugins")),
        # community and unsigned plugins run isolated: on | strict (only with Landlock) | off
        LABDEX_PLUGIN_SANDBOX=(os.environ.get("LABDEX_PLUGIN_SANDBOX", "on").strip().lower() or "on"),
        LABDEX_SSO_HEADER=os.environ.get("LABDEX_SSO_HEADER", ""),
        LABDEX_SSO_GROUPS_HEADER=os.environ.get("LABDEX_SSO_GROUPS_HEADER", ""),
        LABDEX_SSO_AUTOCREATE=_env_bool("LABDEX_SSO_AUTOCREATE", False),
        LABDEX_TRUSTED_PROXIES=_env_list("LABDEX_TRUSTED_PROXIES"),
        # OpenID Connect sign-in (see oidc.py)
        LABDEX_OIDC_ISSUER=os.environ.get("LABDEX_OIDC_ISSUER", "").strip(),
        LABDEX_OIDC_CLIENT_ID=os.environ.get("LABDEX_OIDC_CLIENT_ID", "").strip(),
        LABDEX_OIDC_CLIENT_SECRET=os.environ.get("LABDEX_OIDC_CLIENT_SECRET", "").strip(),
        LABDEX_OIDC_NAME=os.environ.get("LABDEX_OIDC_NAME", "").strip(),
        LABDEX_OIDC_SCOPES=os.environ.get("LABDEX_OIDC_SCOPES", "openid profile email groups").strip(),
        LABDEX_OIDC_USERNAME_CLAIM=os.environ.get("LABDEX_OIDC_USERNAME_CLAIM", "preferred_username").strip(),
        LABDEX_OIDC_GROUPS_CLAIM=os.environ.get("LABDEX_OIDC_GROUPS_CLAIM", "groups").strip(),
        LABDEX_OIDC_ADMIN_GROUPS=os.environ.get("LABDEX_OIDC_ADMIN_GROUPS", "").strip(),
        LABDEX_OIDC_AUTOCREATE=_env_bool("LABDEX_OIDC_AUTOCREATE", True),
        LABDEX_OIDC_ONLY=_env_bool("LABDEX_OIDC_ONLY", False),
        LABDEX_SCHEDULER=_env_bool("LABDEX_SCHEDULER", not testing),
        LABDEX_JOBS_SYNC=False,
        LABDEX_WORKER=os.environ.get("LABDEX_WORKER", "inline").strip().lower(),
        LABDEX_DEMO=_env_bool("LABDEX_DEMO", False),
        SESSION_COOKIE_NAME="labdex_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=_env_bool("LABDEX_COOKIE_SECURE", False),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.environ.get("LABDEX_SESSION_HOURS", "12"))),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith(("postgres://", "postgresql://")):
        # bare postgres URLs use psycopg 3 (the driver labdex ships with)
        app.config["SQLALCHEMY_DATABASE_URI"] = uri = "postgresql+psycopg://" + uri.split("://", 1)[1]
    if not uri.startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20}
    if not app.config.get("SECRET_KEY"):
        from .security import load_secret_key
        app.config["SECRET_KEY"] = load_secret_key(data_dir)
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///") and not testing:
        data_dir.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        with app.app_context():
            # WAL lets the CLI work while the server runs.
            event.listen(db.engine, "connect", _sqlite_pragmas)

    from . import api, cli, naming, rbac
    from .plugins import Registry, jobs
    from .security import Vault

    app.extensions["labdex"] = {
        "registry": Registry(None),  # replaced below once the database (with trusted keys) is ready
        "vault": Vault(app.config["SECRET_KEY"]),
    }
    app.register_blueprint(api.bp)
    cli.register(app)

    with app.app_context():
        from . import schema
        upgraded = schema.upgrade()
        if upgraded:
            app.logger.warning("database upgraded: %s", "; ".join(upgraded))
        naming.ensure_defaults_in_session()
        rbac.ensure_builtin_roles()
        from . import hostnames
        hostnames.ensure_seeded()
        from . import funpools
        funpools.ensure_builtin()
        db.session.commit()
        from .plugins import installer
        installer.reload_registry(app)
        if not testing and not app.config["LABDEX_DEMO"]:
            installer.ensure_official_repository()
        if app.config.get("LABDEX_AUTO_BACKUP", not testing) and not app.config["LABDEX_DEMO"]:
            _ensure_backup_module(app)
        _bootstrap_admin()
        from . import demo
        demo.ensure(app)
        if app.config["SQLALCHEMY_DATABASE_URI"] not in ("sqlite://", "sqlite:///:memory:"):
            db.engine.dispose()  # no connections inherited across a fork
    jobs.init(app)

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok", "version": __version__})

    # The built Vue app: static assets, with index.html for every other path.
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        target = STATIC_DIR / path
        if path and target.is_file() and target.resolve().is_relative_to(STATIC_DIR.resolve()):
            return httpcache.static_file(STATIC_DIR, path)
        if (STATIC_DIR / "index.html").is_file():
            return httpcache.static_file(STATIC_DIR, "index.html")
        return "labdex API is running. The web UI isn't built; see README (npm run build).", 200

    @app.after_request
    def security_headers(resp):
        resp = httpcache.api_etag(resp)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if app.config["SESSION_COOKIE_SECURE"]:  # served over HTTPS: browsers should keep it that way
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        return resp

    return app


def _ensure_backup_module(app) -> None:
    """Backups are part of the core: a daily local backup exists from the first start
    (unencrypted until you set a passphrase; the page says so)."""
    from .models import Integration
    if db.session.scalar(db.select(Integration).where(Integration.plugin_id == "backup")) is not None:
        return
    if app.extensions["labdex"]["registry"].get("backup") is None:
        return
    db.session.add(Integration(plugin_id="backup", name="backup", enabled=True,
                               settings={"target": "folder", "path": "", "keep": 14, "schedule_backup": 1440},
                               state={}, selectors=[]))
    db.session.commit()


def _bootstrap_admin() -> None:
    """LABDEX_ADMIN_USER + LABDEX_ADMIN_PASSWORD create the first admin on an empty database."""
    from . import security
    from .models import User

    user, password = os.environ.get("LABDEX_ADMIN_USER"), os.environ.get("LABDEX_ADMIN_PASSWORD")
    if not user or not password:
        return
    if db.session.scalar(db.select(db.func.count()).select_from(User)):
        return
    db.session.add(User(username=user.lower(), display_name=user, is_admin=True,
                        password_hash=security.hash_password(password)))
    db.session.commit()
