import os
from pathlib import Path

import pytest

from labdex import create_app, inventory
from labdex.models import db

EXAMPLE = Path(__file__).parents[2] / "examples" / "homelab.yaml"
ADMIN = {"username": "root", "password": "correct-horse-battery"}
PASSWORD = "user-password-123"


def client(app, **environ):
    c = app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "labdex"
    c.environ_base.update(environ)
    return c


# LABDEX_TEST_DATABASE_URL=postgresql://user@host:port/db runs every test on Postgres
# (the database is wiped before each test). Default: a fresh SQLite file per test.
PG_URL = os.environ.get("LABDEX_TEST_DATABASE_URL", "")


def _fresh_postgres(url: str) -> str:
    from sqlalchemy import create_engine, text
    url = "postgresql+psycopg://" + url.split("://", 1)[1]
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    eng.dispose()
    return url


@pytest.fixture
def app(tmp_path, request):
    uri = _fresh_postgres(PG_URL) if PG_URL else f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": uri,
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "LABDEX_PLUGIN_DIR": str(tmp_path / "plugins"),
        "LABDEX_JOBS_SYNC": True,
        "LABDEX_SCHEDULER": False,
    })
    from labdex import auth
    auth._failures.clear()
    with app.app_context():
        if not request.node.get_closest_marker("core_plugins_only"):
            # most tests use plugins outside the core set; @pytest.mark.core_plugins_only: as a new labdex has them
            from labdex.plugins import catalog
            reg = app.extensions["labdex"]["registry"]
            catalog._save({s.id for s in reg.specs.values() if s.bundled})
            db.session.commit()
        yield app
        db.session.remove()
        if PG_URL:
            db.engine.dispose()


@pytest.fixture
def loaded(app):
    inventory.import_yaml(EXAMPLE.read_text())
    # most tests check alias names on the firewall; uids (the default since 0.10.5) have their own tests
    from labdex import store
    from labdex.models import db
    store.save_settings({"alias_ids": "readable"})
    db.session.commit()
    return app


@pytest.fixture
def admin(loaded):
    c = client(loaded)
    r = c.post("/api/auth/setup", json=ADMIN)
    assert r.status_code == 201, r.get_json()
    return c


@pytest.fixture
def make_user(loaded, admin):
    """make_user("alice", role="editor", scopes=["zone:DC_TC"]) -> logged-in client"""
    def make(name, role=None, scopes=None, group=None, grants=()):
        r = admin.post("/api/admin/users", json={"username": name, "password": PASSWORD})
        assert r.status_code == 201, r.get_json()
        if group:
            admin.post("/api/admin/groups", json={"name": group, "members": [name]})
        if role:
            subj = {"group": group} if group else {"user": name}
            r = admin.post("/api/admin/bindings", json={**subj, "role": role, "scopes": scopes or ["*"]})
            assert r.status_code == 201, r.get_json()
        for g in grants:
            r = admin.post("/api/admin/grants", json={"user": name, **g})
            assert r.status_code == 201, r.get_json()
        c = client(loaded)
        assert c.post("/api/auth/login", json={"username": name, "password": PASSWORD}).status_code == 200
        return c
    return make


@pytest.fixture
def vault(admin):
    """The vault set up and unsealed (secrets live there since 1.18); sealed again afterwards."""
    from labdex import keyvault
    keyvault.seal()
    keyvault._tries.clear()
    r = admin.post("/api/vault/init", json={"passphrase": "test vault passphrase"})
    assert r.status_code == 200, r.get_json()
    yield admin
    keyvault.seal()
