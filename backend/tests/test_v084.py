"""v0.8.4: routing discovery (OPNsense FRR, Cisco IOS-XE RESTCONF)."""
import pytest

from labdex.models import Notification, db

from .fakes import FakeRouter, FakeServer
from .test_plugins import make_instance, run


@pytest.fixture
def router():
    fake = FakeRouter()
    srv = FakeServer(fake)
    fake.url = srv.url
    yield fake
    srv.close()


@pytest.mark.parametrize("source", ["opnsense_frr", "cisco_restconf"])
def test_routes_and_neighbours(admin, router, source):
    make_instance(admin, "routing", "rt", {"source": source, "url": router.url, "username": "k", "password": "s"})
    assert run(admin, "rt", "test")["result"]["message"] == "5 routes, 2 BGP neighbour(s)"
    job = run(admin, "rt", "read")
    assert job["result"]["established"] == 1 and job["result"]["unknown"] == 1, job
    page = admin.get("/api/integrations/rt/pages/routes").get_json()
    neigh = {n["peer"]: n for n in page["sections"][0]["rows"]}
    assert neigh["10.10.3.254"]["host"] == "net-rt-oob-01" and neigh["192.0.2.1"]["_tone"] == "err"
    unknown = page["sections"][1]["rows"]
    assert [u["prefix"] for u in unknown] == ["10.20.0.0/16"] and unknown[0]["protocol"] == "bgp"
    no_route = {r["network"] for r in page["sections"][2]["rows"]}
    assert "srv" not in no_route and "main" not in no_route and "mgmt" in no_route

    # ignoring the prefix; and a neighbour going down sends a notification
    admin.put("/api/integrations/rt", json={"settings": {"ignore": ["10.20.0.0/16"]}})
    admin.post("/api/notification-channels", json={"name": "hook", "kind": "webhook", "settings": {"url": "http://127.0.0.1:9/x"},
                                                   "events": ["plugin.routing.*"]})
    router.peers["10.10.3.254"]["state"] = "Idle"
    job = run(admin, "rt", "read")
    assert job["result"]["unknown"] == 0
    n = db.session.scalar(db.select(Notification))
    assert n.event == "plugin.routing.neighbour_down" and "10.10.3.254" in n.title
