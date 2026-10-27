"""The 1.0 gate: a database from 0.5 starts on the current release with no manual step.

tests/fixtures/labdex-0.5.sqlite3 has the 0.5.0 schema (Alembic baseline) with the example
homelab, trust levels (areas × trust zones) and an admin (admin / fixture-password-1)."""
import shutil
from pathlib import Path

import pytest
import yaml

from labdex import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "labdex-0.5.sqlite3"


@pytest.fixture
def old_app(tmp_path, monkeypatch):
    if not FIXTURE.is_file():
        pytest.skip("no 0.5 fixture")
    db_file = tmp_path / "labdex.db"
    shutil.copy(FIXTURE, db_file)
    monkeypatch.setenv("LABDEX_DATA", str(tmp_path))
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file}", "TESTING": True, "SECRET_KEY": "fixture-secret",
                      "LABDEX_PLUGIN_DIR": str(tmp_path / "plugins")})
    yield app


def test_upgrade_from_0_5(old_app):
    from labdex import schema
    from labdex.models import db
    with old_app.app_context():
        with db.engine.begin() as conn:
            assert schema.current(conn) == schema.head()
    c = old_app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "labdex"
    assert c.post("/api/auth/login", json={"username": "admin", "password": "fixture-password-1"}).status_code == 200
    hosts = c.get("/api/hosts").get_json()
    assert len(hosts) == 15 and next(h for h in hosts if h["name"] == "srv-vm-dns-01")["zone"] == "DC_TC"
    zones = {z["code"]: z for z in c.get("/api/zones").get_json()["zones"]}
    assert zones["DC_TC"]["name"] == "Datacenter, Trusted-Compliant" and zones["DC_TC"]["rank"] == 4 and zones["DC"]["default"]
    assert c.get("/api/lint").status_code == 200
    # nothing on the firewall is renamed by the upgrade
    assert c.get("/api/meta").get_json()["settings"]["alias_ids"] == "readable"
    names = {a["fw_id"] for a in c.get("/api/aliases").get_json()}
    assert "NG_ZONE_DC_TC" in names
    # tags came along, pages answer, the YAML round trip holds
    assert {t["name"] for t in c.get("/api/tags").get_json()} >= {"admin"}
    for url in ("/api/dashboard", "/api/dns", "/api/zone-policy", "/api/plugins", "/api/nav", "/api/about"):
        assert c.get(url).status_code == 200, url
    data = yaml.safe_load(c.get("/api/admin/backup").get_data(as_text=True))
    assert "zones" in data and "trust_levels" not in data and len(data["hosts"]) == 15
    # writes work
    h = c.get("/api/hosts/srv-vm-dns-01").get_json()
    assert c.put("/api/hosts/srv-vm-dns-01", json={**h, "tags": ["dns"]}).status_code == 200
