"""1.21: what ships with labdex isn't all installed; the Marketplace installs it (offline, instantly)."""
import pytest

from labdex.plugins import catalog

CORE = {"backup", "exports", "ansible", "nmap"}


@pytest.mark.core_plugins_only
def test_a_new_labdex_has_the_core_set(admin, make_user):
    ids = {p["id"] for p in admin.get("/api/plugins").get_json()["plugins"]}
    assert ids - {"demo-lab"} == CORE
    assert admin.get("/api/plugins").get_json()["available"] >= 15
    r = admin.post("/api/integrations", json={"plugin_id": "opnsense", "name": "fw"})
    assert r.status_code == 422 and "install it from the Marketplace" in str(r.get_json())

    m = admin.get("/api/marketplace?offline=1").get_json()
    opn = next(p for p in m["plugins"] if p["id"] == "opnsense")
    assert (opn["installed"], opn["bundled"], opn["source"], opn["official"]) == (False, True, "ships with labdex", True)
    assert opn["actions"] and opn["settings"] and m["can_install"] is True
    assert next(p for p in m["plugins"] if p["id"] == "backup")["installed"] is True

    # viewers browse, only administrators install
    v = make_user("v", role="viewer")
    assert v.get("/api/marketplace?offline=1").status_code == 200
    assert v.post("/api/marketplace/install", json={"id": "opnsense"}).status_code == 403

    r = admin.post("/api/marketplace/install", json={"id": "opnsense"})
    assert r.status_code == 201 and r.get_json()["installed"] is True
    assert admin.post("/api/marketplace/install", json={"id": "opnsense"}).status_code == 200  # again: nothing to do
    assert "opnsense" in {p["id"] for p in admin.get("/api/plugins").get_json()["plugins"]}
    assert admin.post("/api/integrations", json={"plugin_id": "opnsense", "name": "fw"}).status_code == 201
    hist = admin.get("/api/history?object_type=plugin").get_json()
    assert any("installed OPNsense" in h["summary"] for h in hist)

    # uninstalling: not while an instance exists; then it's just no longer offered
    assert admin.delete("/api/plugins/opnsense").status_code == 409
    assert admin.delete("/api/integrations/fw").status_code == 204
    assert admin.delete("/api/plugins/opnsense").status_code == 204
    assert "opnsense" not in {p["id"] for p in admin.get("/api/plugins").get_json()["plugins"]}
    assert admin.get("/api/plugins/opnsense").get_json()["installed"] is False


@pytest.mark.core_plugins_only
def test_an_upgrade_keeps_what_is_used(app):
    """Before the migration decided (or on a database it never ran on): the core set and what has instances."""
    from labdex.models import Integration, Setting, db
    db.session.delete(db.session.get(Setting, catalog.SETTING))
    db.session.add(Integration(plugin_id="unifi", name="u", enabled=False, settings={}, state={}))
    db.session.commit()
    assert catalog.installed_bundled() == CORE | {"unifi"}


def test_the_migration_keeps_what_is_used(tmp_path):
    """0033 on a database with instances: core plus those."""
    import json
    import sqlalchemy as sa
    from labdex import create_app
    from labdex.models import Integration, Setting, db
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'm.db'}", "TESTING": True, "SECRET_KEY": "x",
                      "LABDEX_PLUGIN_DIR": str(tmp_path / "p"), "LABDEX_SCHEDULER": False})
    with app.app_context():
        db.session.add(Integration(plugin_id="technitium", name="dns", enabled=False, settings={}, state={}))
        db.session.delete(db.session.get(Setting, catalog.SETTING))
        db.session.commit()
        import importlib
        mig = importlib.import_module("labdex.migrations.versions.0033_bundled_installed")
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        with db.engine.begin() as conn:
            ops = Operations(MigrationContext.configure(conn))
            mig.op = ops
            mig.upgrade()
        row = db.session.execute(sa.text("SELECT value FROM setting WHERE key = 'bundled_installed'")).scalar()
        assert set(json.loads(row)) == CORE | {"technitium"}
