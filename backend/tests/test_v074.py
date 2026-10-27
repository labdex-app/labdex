"""v0.7.4: Uptime Kuma."""
import pytest

from labdex import widgets
from labdex.models import HostStatus, db

from .fakes import FakeKuma
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


@pytest.fixture
def kuma(loaded):
    # the test server speaks only polling (no websocket upgrade)
    loaded.extensions["labdex"]["registry"].get("uptimekuma").module.Kuma.TRANSPORTS = ["polling"]
    k = FakeKuma()
    yield k
    k.close()


def test_monitors_plan_apply_and_status(admin, kuma):
    kuma.monitors = {7: {"id": 7, "name": "router", "type": "ping", "hostname": "10.10.100.20", "description": "", "active": True}}
    kuma.status = {7: 0}
    make_instance(admin, "uptimekuma", "kuma", {"url": kuma.url, "username": "admin", "password": "pw", "notifications": ["3"]})
    assert run(admin, "kuma", "test")["result"]["message"] == "1 monitors, 0 managed by labdex"

    plan = run(admin, "kuma", "sync_monitors")
    detail = admin.get(f"/api/jobs/{plan['id']}").get_json()
    created = {c["name"]: c for c in detail["plan"]}
    assert {c["op"] for c in detail["plan"]} == {"create"} and "unown dns-admin" in created
    assert created["unown dns-admin"]["after"]["url"].startswith("http://dns.") and created["unown dns-admin"]["after"]["type"] == "http"
    assert not any("dns " in n for n in created if n.endswith(" dns"))  # the udp service has no link
    done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    added = [d for op, d in kuma.calls if op == "add"]
    assert len(added) == len(created) and added[0]["notificationIDList"] == {"3": True} and added[0]["description"].startswith("labdex:")
    assert run(admin, "kuma", "sync_monitors")["status"] == "succeeded"  # nothing left to do

    # a changed link updates the monitor; a removed service deletes it
    h = host(admin, "srv-vm-dns-01")
    svcs = [dict(s, path="/admin") if s["name"] == "dns-admin" else s for s in h["services"]]
    put_host(admin, "srv-vm-dns-01", services=svcs)
    put_host(admin, "srv-vm-ha-01", services=[])
    p = admin.get("/api/jobs?integration=kuma&status=planned").get_json()[0]
    ops = {(c["op"], c["name"]) for c in admin.get(f"/api/jobs/{p['id']}").get_json()["plan"]}
    assert ("update", "unown dns-admin") in ops and ("delete", "rotom homeassistant") in ops
    admin.post(f"/api/jobs/{p['id']}/approve")
    assert any(op == "delete" for op, _ in kuma.calls) and any(op == "edit" and d["url"].endswith("/admin") for op, d in kuma.calls)

    # status: managed monitors by key, others matched by address
    mid = next(k for k, m in kuma.monitors.items() if m.get("description") == "labdex:srv-vm-dns-01/dns-admin")
    kuma.status[mid] = 1
    job = run(admin, "kuma", "poll")
    assert job["status"] == "succeeded", job
    rows = {r.host: r for r in db.session.scalars(db.select(HostStatus))}
    assert rows["srv-vm-dns-01"].state == "up" and rows["srv-vm-dns-01"].data["services"]["dns-admin"] == "up"
    assert rows["srv-vm-mon-01"].state == "down"
    svc = next(s for s in admin.get("/api/services").get_json() if s["host"] == "srv-vm-dns-01" and s["name"] == "dns-admin")
    assert svc["status"] == "open" and svc["status_by"] == "kuma"

    widgets.clear()
    w = next(x for x in admin.get("/api/widgets").get_json() if x["integration"] == "kuma")["data"]
    assert w["items"][0]["label"] == "router" and w["items"][0]["state"] == "down"
    hw = admin.get("/api/hosts/srv-vm-dns-01/widgets").get_json()[0]["data"]
    assert hw["items"][0]["state"] == "up"


def test_bad_login(admin, kuma):
    make_instance(admin, "uptimekuma", "kuma", {"url": kuma.url, "username": "admin", "password": "wrong"})
    job = run(admin, "kuma", "test")
    assert job["status"] == "failed" and "refused the login" in job["result"]["error"]
