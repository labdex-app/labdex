"""v0.7.3: Beszel."""
import pytest

from labdex import widgets
from labdex.models import HostStatus, db

from .fakes import FakeBeszel, FakeServer
from .test_plugins import make_instance, run


@pytest.fixture
def hub():
    fake = FakeBeszel()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.systems = [
        {"id": "a", "name": "dns", "host": "10.10.100.53", "port": "45876", "status": "up",
         "info": {"cpu": 12.3, "mp": 81.0, "dp": 40.0, "u": 864000, "v": "0.18.0", "la": [0.1, 0.2, 0.3]}},
        {"id": "b", "name": "srv-vm-mon-01", "host": "beszel-unknown.example", "port": "45876", "status": "down", "info": {}},
        {"id": "c", "name": "laptop", "host": "192.0.2.9", "port": "45876", "status": "paused", "info": {}},
    ]
    yield fake
    srv.close()


def test_beszel_status_widgets_and_sync(admin, hub):
    make_instance(admin, "beszel", "bz", {"url": hub.url, "email": "me@lab", "password": "pw"})
    assert run(admin, "bz", "test")["result"]["message"] == "3 systems in Beszel"
    job = run(admin, "bz", "poll")
    assert job["result"] == {"hosts": 2, "unmatched": 1}
    rows = {r.host: r for r in db.session.scalars(db.select(HostStatus))}
    assert rows["srv-vm-dns-01"].state == "up" and "memory 81 %" in rows["srv-vm-dns-01"].detail
    assert rows["srv-vm-mon-01"].state == "down" and rows["srv-vm-dns-01"].data["mp"] == 81.0

    widgets.clear()
    w = admin.get("/api/hosts/srv-vm-dns-01/widgets").get_json()[0]["data"]
    assert [i["label"] for i in w["items"]] == ["CPU", "memory", "disk", "load"] and w["items"][1]["tone"] == "warn"
    assert w["sub"] == "up 10 d · agent 0.18.0"
    d = next(x for x in admin.get("/api/widgets").get_json() if x["integration"] == "bz")["data"]
    assert d["items"][0]["label"] == "srv-vm-mon-01" and d["items"][0]["state"] == "down" and d["sub"] == "3 systems, 1 down"

    # only hosts in DC: plan adds the ones Beszel doesn't know, with their primary address
    admin.put("/api/integrations/bz", json={"selectors": "zone:DC_*"})
    plan = run(admin, "bz", "sync_systems")
    names = {c["name"] for c in admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]}
    assert "srv-vm-dns-01" not in names and "srv-vm-mon-01" not in names and "srv-vm-ha-01" in names
    assert "net-vip-k3s-01" not in names  # VIPs aren't machines
    done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied" and len(hub.created) == len(names)
    ha = next(c for c in hub.created if c["name"] == "srv-vm-ha-01")
    assert ha == {"name": "srv-vm-ha-01", "host": "10.10.100.30", "port": "45876", "users": ["u1"]}


def test_beszel_bad_login_and_agent_needs_key(admin, hub):
    make_instance(admin, "beszel", "bz", {"url": hub.url, "email": "me@lab", "password": "nope"})
    job = run(admin, "bz", "test")
    assert job["status"] == "failed"
    admin.put("/api/integrations/bz", json={"settings": {"password": "pw"}})
    r = admin.post("/api/hosts/srv-vm-dns-01/modules/bz/actions/install_agent")
    j = admin.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert j["status"] == "failed" and "public key" in j["result"]["error"]
