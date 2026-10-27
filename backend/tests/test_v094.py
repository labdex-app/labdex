"""v0.9.4: demo mode."""
from datetime import datetime, timedelta

import pytest

from labdex import create_app, demo
from labdex.models import Host, Integration, Job, User, ZonePolicy, db, utcnow
from labdex.plugins import jobs


@pytest.fixture
def demo_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LABDEX_DATA", str(tmp_path))
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'demo.db'}", "LABDEX_DEMO": True,
                      "LABDEX_JOBS_SYNC": True, "LABDEX_PLUGIN_DIR": str(tmp_path / "plugins"), "SECRET_KEY": "x" * 32})
    yield app


def login(app):
    c = app.test_client()
    c.environ_base["HTTP_X_REQUESTED_WITH"] = "labdex"
    st = c.get("/api/auth/status").get_json()
    assert st["demo"]["username"] == "demo"
    assert c.post("/api/auth/login", json={"username": st["demo"]["username"], "password": st["demo"]["password"]}).status_code == 200
    return c


def test_seeded_on_start(demo_app):
    with demo_app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Host)) == 15
        assert db.session.scalar(db.select(db.func.count()).select_from(ZonePolicy)) == 6
        assert {i.plugin_id for i in db.session.scalars(db.select(Integration))} == {"demo-lab", "exports"}
        acts = {(j.action, j.status) for j in db.session.scalars(db.select(Job))}
        assert ("poll", "succeeded") in acts and ("sync", "planned") in acts, acts
    c = login(demo_app)
    meta = c.get("/api/meta").get_json()
    assert meta["demo"]["resets_at"].endswith("03:00:00Z")
    assert c.get("/api/dashboard").get_json()["counts"]["down"] == 1  # one host has a bad day
    assert [d["name"] for d in c.get("/api/dashboards").get_json()["dashboards"]] == ["Overview"]  # the start page survives the wipe (1.24)
    w = c.get("/api/widgets").get_json()
    assert {x["id"]: x["data"]["kind"] for x in w} == {"traffic": "sparkline", "records": "stat"}, w
    plan = next(j for j in c.get("/api/jobs?integration=lab&status=planned").get_json() if j["action"] == "sync")
    done = c.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied"
    assert c.get("/api/integrations/lab/pages/lab").get_json()["summary"][0]["value"] > 20


def test_demo_refuses(demo_app):
    c = login(demo_app)
    assert c.post("/api/auth/password", json={"current": "demo-labdex", "new": "something-else-1"}).status_code == 403
    assert c.post("/api/admin/users", json={"username": "x"}).status_code == 403
    assert c.post("/api/plugin-repos/preview", json={"url": "http://example.com/index.json"}).status_code == 403
    assert c.post("/api/notification-channels", json={}).status_code == 403
    r = c.post("/api/integrations", json={"plugin_id": "opnsense", "name": "fw", "settings": {}})
    assert r.status_code == 422 and "demo" in str(r.get_json())
    with demo_app.app_context():
        db.session.add(Integration(plugin_id="nmap", name="scan", enabled=True, settings={}, selectors=["*"], state={}))
        db.session.commit()
        with pytest.raises(Exception, match="doesn't run in the demo"):
            jobs.start(db.session.scalar(db.select(Integration).where(Integration.name == "scan")), "scan")
    # editing the inventory works
    h = c.get("/api/hosts/srv-vm-dns-01").get_json()
    assert c.put("/api/hosts/srv-vm-dns-01", json={**h, "function": "changed in the demo"}).status_code == 200


def test_nightly_reset(demo_app):
    with demo_app.app_context():
        h = db.session.scalar(db.select(Host).where(Host.name == "srv-vm-dns-01"))
        db.session.delete(h)
        i = db.session.scalar(db.select(Integration).where(Integration.plugin_id == "demo-lab"))
        i.state = {**i.state, "demo_seeded_at": (utcnow() - timedelta(days=1, hours=1)).isoformat()}
        db.session.commit()
        assert demo.maybe_reset()
        assert db.session.scalar(db.select(Host).where(Host.name == "srv-vm-dns-01")) is not None
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1
        assert not demo.maybe_reset()
    assert demo.next_reset(datetime(2026, 1, 1, 2, 59)) == datetime(2026, 1, 1, 3, 0)
    assert demo.next_reset(datetime(2026, 1, 1, 3, 0)) == datetime(2026, 1, 2, 3, 0)


def test_hidden_without_demo(admin):
    assert not any(p["id"] == "demo-lab" for p in admin.get("/api/plugins").get_json()["plugins"])
    assert admin.get("/api/auth/status").get_json()["demo"] is None
