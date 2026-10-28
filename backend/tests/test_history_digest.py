"""Net changes between two points in time, `labdex diff`, and the weekly digest."""
from datetime import timedelta

import pytest

from labdex import audit, notify
from labdex.models import Notification, NotificationChannel, Setting, db, utcnow


def test_net_change_per_object(admin):
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "function": "One"})
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "function": "Two"})
    admin.post("/api/hosts", json={"name": "srv-vm-tmp-01", "zone": "DC_TC"})
    admin.post("/api/hosts", json={"name": "srv-vm-tmp-02", "zone": "DC_TC"})
    admin.delete("/api/hosts/srv-vm-tmp-02")  # created and deleted: not listed
    admin.delete("/api/hosts/srv-vm-k3s-02")

    r = admin.get("/api/history/changes?since=1h")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    rows = {(o["object_name"]): o for o in d["objects"] if o["object_type"] == "host"}
    assert rows["srv-vm-dns-01"]["change"] == "updated" and rows["srv-vm-dns-01"]["events"] == 2
    assert [x["after"] for x in rows["srv-vm-dns-01"]["diff"] if x["path"] == "function"] == ["Two"]
    assert rows["srv-vm-tmp-01"]["change"] == "created"
    assert rows["srv-vm-k3s-02"]["change"] == "deleted"
    assert "srv-vm-tmp-02" not in rows
    assert d["counts"]["created"] >= 1 and d["counts"]["deleted"] >= 1


def test_change_and_change_back_is_nothing(admin):
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "function": "Temporary"})
    admin.put("/api/hosts/srv-vm-dns-01", json=h)
    names = {o["object_name"] for o in admin.get("/api/history/changes?since=1h").get_json()["objects"]}
    assert "srv-vm-dns-01" not in names


def test_since_forms(admin):
    assert admin.get("/api/history/changes?since=nonsense").status_code == 422
    assert admin.get("/api/history/changes?since=2020-01-01").status_code == 200
    assert audit.parse_since("2d", now=utcnow()) < utcnow() - timedelta(days=1, hours=23)


def test_viewers_only_see_what_they_may(admin, make_user):
    admin.post("/api/hosts", json={"name": "srv-vm-tmp-01", "zone": "DC_TC"})
    c = make_user("vi", role="viewer", scopes=["zone:CA_*"])
    objs = c.get("/api/history/changes?since=1h").get_json()["objects"]
    assert all(o["object_name"] != "srv-vm-tmp-01" for o in objs)


@pytest.fixture
def channel(loaded):
    ch = NotificationChannel(name="w", kind="webhook", settings={}, events=["digest.weekly"], integrations=["*"])
    db.session.add(ch)
    db.session.commit()
    return ch


def test_digest_starts_its_clock_then_fires_weekly(channel):
    now = utcnow()
    assert notify.digest_due(now) is False          # first check only starts the clock
    assert db.session.get(Setting, notify.DIGEST_KEY).value
    assert notify.digest_due(now + timedelta(days=6)) is False
    assert notify.digest_due(now + timedelta(days=7, minutes=1)) is True
    sent = db.session.scalars(db.select(Notification).where(Notification.event == "digest.weekly")).all()
    assert len(sent) == 1 and "Lint:" in sent[0].body and "Plans waiting" in sent[0].body
    assert notify.digest_due(now + timedelta(days=8)) is False


def test_no_digest_without_a_subscriber(loaded):
    assert notify.digest_due(utcnow() + timedelta(days=30)) is False
    assert db.session.get(Setting, notify.DIGEST_KEY) is None


def test_diff_command(admin):
    admin.post("/api/hosts", json={"name": "srv-vm-tmp-01", "zone": "DC_TC"})
    db.session.expire_all()
    from flask.testing import FlaskCliRunner
    app = admin.application
    out = FlaskCliRunner(app).invoke(args=["diff", "--since", "1h"])
    assert out.exit_code == 0, out.output
    assert "+ host" in out.output and "srv-vm-tmp-01" in out.output
