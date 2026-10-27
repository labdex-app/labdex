"""Demo mode (LABDEX_DEMO=1): a sample lab anyone may click through.

- On an empty database, and every night at 03:00 UTC, everything is wiped and the sample
  lab (labdex/demo_lab.yaml) is loaded again, with a demo admin and a pretend DNS server
  and monitor (the built-in `demo-lab` plugin) that never leave labdex.
- Nothing talks to a network: only the demo-lab and exports plugins can have instances
  or run, plugins can't reach out (ctx.http and Ansible refuse), and installing plugins,
  repositories, notification channels, restores and changes to users and passwords are off.
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
from pathlib import Path

from flask import current_app, has_app_context, request

from .models import db

USER, PASSWORD = "demo", "demo-labdex"
PLUGINS = {"demo-lab", "exports"}
RESET_HOUR = 3  # UTC
LAB = Path(__file__).parent / "demo_lab.yaml"
BLOCKED = (  # writes refused in the demo: (method or *, path prefix)
    ("*", "/api/auth/password"), ("*", "/api/auth/setup"),
    ("*", "/api/admin/users"), ("*", "/api/admin/restore"), ("*", "/api/admin/backup"),
    ("*", "/api/plugins/upload"), ("*", "/api/plugins/stage"), ("*", "/api/plugins/keys"), ("DELETE", "/api/plugins/"),
    ("*", "/api/plugin-repos"), ("*", "/api/marketplace/install"),
    ("*", "/api/notification-channels"), ("*", "/api/secrets"), ("*", "/api/vault"),  # one visitor's passphrase would lock out the rest
)


def enabled(app=None) -> bool:
    if app is None:
        if not has_app_context():
            return False
        app = current_app
    return bool(app.config.get("LABDEX_DEMO"))


def guard():
    """before_request: the demo is shared, so nothing that locks others out or reaches out."""
    if not enabled() or request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    for method, prefix in BLOCKED:
        if (method == "*" or method == request.method) and request.path.startswith(prefix):
            from .api import err
            return err(403, "not in the demo: it's shared and resets every night")
    return None


def next_reset(after: datetime) -> datetime:
    at = after.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    return at if at > after else at + timedelta(days=1)


def _integration():
    from .models import Integration
    return db.session.scalar(db.select(Integration).where(Integration.plugin_id == "demo-lab"))


def seeded_at() -> datetime | None:
    i = _integration()
    v = (i.state or {}).get("demo_seeded_at") if i else None
    return datetime.fromisoformat(v) if v else None


def info() -> dict | None:
    if not enabled():
        return None
    at = seeded_at() or datetime.now(UTC).replace(tzinfo=None)
    return {"username": USER, "password": PASSWORD, "resets_at": next_reset(at).isoformat() + "Z"}


def wipe() -> None:
    from . import cache
    db.session.rollback()
    db.session.expunge_all()
    for t in reversed(db.metadata.sorted_tables):
        db.session.execute(t.delete())
    db.session.flush()
    cache.bump()


def seed() -> None:
    """Wipe everything and load the sample lab."""
    from . import funpools, hostnames, inventory, naming, rbac, security
    from .models import ApprovalPolicy, Integration, User
    from .plugins import jobs
    wipe()
    naming.ensure_defaults_in_session()
    rbac.ensure_builtin_roles()
    hostnames.ensure_seeded()
    funpools.ensure_builtin()
    db.session.flush()
    inventory.import_yaml(LAB.read_text(), commit=False)
    from . import dashboards
    dashboards.ensure_overview()  # the wipe took the start page too
    db.session.add(User(username=USER, display_name="Demo", is_admin=True, password_hash=security.hash_password(PASSWORD)))
    db.session.add(ApprovalPolicy(name="demo", position=0, approvals=1, self_approval=True,
                                  description="One approval, and you may approve your own plans"))
    now = datetime.now(UTC).replace(tzinfo=None)
    lab = Integration(plugin_id="demo-lab", name="lab", enabled=True, settings={}, selectors=["*"],
                      state={"demo_seeded_at": now.isoformat()})
    db.session.add(lab)
    db.session.add(Integration(plugin_id="exports", name="exports", enabled=True, settings={}, selectors=["*"], state={}))
    from . import audit
    audit.record("import", "inventory", "demo", summary="loaded the demo's sample lab", actor=("demo reset", "system"))
    db.session.commit()
    if current_app.extensions["labdex"]["registry"].get("demo-lab") is not None:
        for action in ("poll", "sync"):
            try:
                jobs.start(lab, action, trigger="manual", created_by=USER)
            except Exception:  # the demo still works without the first runs
                current_app.logger.exception("demo: starting %s failed", action)
                db.session.rollback()
    current_app.logger.warning("demo: sample lab loaded; next reset %s UTC", next_reset(now).isoformat())


def ensure(app) -> None:
    """At start: load the sample lab into an empty (or never seeded) database."""
    from .models import User
    if not enabled(app):
        return
    if seeded_at() is None or not db.session.scalar(db.select(db.func.count()).select_from(User)):
        seed()


def maybe_reset() -> bool:
    if not enabled():
        return False
    at = seeded_at()
    now = datetime.now(UTC).replace(tzinfo=None)
    if at is None or now >= next_reset(at):
        seed()
        return True
    return False
