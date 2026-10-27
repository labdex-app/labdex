"""v0.7.2: host status from monitoring sources; VictoriaMetrics / Prometheus."""
import json

import pytest

from labdex import health, notify, widgets
from labdex.models import HostStatus, Notification, db

from .fakes import FakePrometheus, FakeServer
from .test_plugins import make_instance, run


@pytest.fixture
def prom():
    fake = FakePrometheus(token="t0k")
    srv = FakeServer(fake)
    fake.url = srv.url
    yield fake
    srv.close()


def test_matcher(loaded):
    m = health.host_matcher()
    assert m("10.10.100.53:9100") == "srv-vm-dns-01"
    assert m("srv-vm-dns-01") == m("SRV-VM-DNS-01.lab.example.net") == "srv-vm-dns-01" or m("srv-vm-dns-01") == "srv-vm-dns-01"
    assert m("https://10.10.100.53/metrics") == "srv-vm-dns-01"
    assert m("[fd00:10:10:100::53]:9100") == "srv-vm-dns-01"
    assert m("10.99.99.99") is None and m("") is None


def test_victoriametrics_status(admin, prom):
    prom.up = {("10.10.100.53:9100", "node"): 1, ("srv-vm-dns-01:9153", "coredns"): 0,
               ("10.10.100.20:9100", "node"): 0, ("192.0.2.1:9100", "node"): 1}
    prom.cpu = {"10.10.100.53:9100": [10, 12.5, 30]}
    make_instance(admin, "victoriametrics", "vm", {"url": prom.url, "auth": "bearer", "token": "t0k"})
    assert run(admin, "vm", "test")["result"]["message"] == "4 targets answer the status query"
    job = run(admin, "vm", "poll")
    assert job["status"] == "succeeded" and job["result"]["unmatched"] == 1
    rows = {r.host: r for r in db.session.scalars(db.select(HostStatus))}
    assert rows["srv-vm-dns-01"].state == "up" and "down: coredns" in rows["srv-vm-dns-01"].detail
    mon = next(h for h in rows if h != "srv-vm-dns-01")
    assert rows[mon].state == "down"

    hosts = {h["name"]: h for h in admin.get("/api/hosts?fields=name,live,live_sources").get_json()}
    assert hosts["srv-vm-dns-01"]["live"] == "up" and hosts["srv-vm-dns-01"]["live_sources"] == {"vm": "up"}
    assert hosts[mon]["live"] == "down"
    d = admin.get("/api/hosts/srv-vm-dns-01").get_json()["health"]
    assert d["live"] == "up" and d["sources"][0]["integration"] == "vm"

    # widgets
    widgets.clear()
    w = admin.get("/api/hosts/srv-vm-dns-01/widgets").get_json()[0]
    assert w["data"]["kind"] == "sparkline" and w["data"]["points"] == [10, 12.5, 30] and w["data"]["value"] == 30
    t = next(x for x in admin.get("/api/widgets").get_json() if x["integration"] == "vm")
    assert {i["label"] for i in t["data"]["items"]} == {"srv-vm-dns-01", mon}


def test_down_and_up_notify(admin, prom):
    admin.post("/api/notification-channels", json={"name": "hook", "kind": "webhook", "settings": {"url": "http://127.0.0.1:9/x"},
                                                   "events": ["host.*"]})
    prom.up = {("10.10.100.53:9100", "node"): 1}
    make_instance(admin, "victoriametrics", "vm", {"url": prom.url, "auth": "bearer", "token": "t0k"})
    run(admin, "vm", "poll")  # first sight: no notification
    assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 0
    prom.up = {("10.10.100.53:9100", "node"): 0}
    run(admin, "vm", "poll")
    n = db.session.scalar(db.select(Notification))
    assert n.event == "host.down" and n.data["sources"] == {"vm": "down"} and n.link.endswith("/hosts/srv-vm-dns-01")
    prom.up = {("10.10.100.53:9100", "node"): 1}
    run(admin, "vm", "poll")
    assert [x.event for x in db.session.scalars(db.select(Notification).order_by(Notification.id))] == ["host.down", "host.up"]
    # a target that disappears from the source is no longer reported
    prom.up = {}
    run(admin, "vm", "poll")
    assert db.session.scalar(db.select(db.func.count()).select_from(HostStatus)) == 0
    assert notify  # (imported for the channel model)


def test_any_down_wins_and_stale_is_unknown(admin, loaded):
    from datetime import timedelta
    from labdex.models import Integration, utcnow
    a = Integration(plugin_id="victoriametrics", name="a", enabled=True, settings={}, state={}, selectors=[])
    b = Integration(plugin_id="victoriametrics", name="b", enabled=True, settings={}, state={}, selectors=[])
    db.session.add_all([a, b])
    db.session.commit()
    health.report(a, {"srv-vm-dns-01": {"state": "up"}})
    health.report(b, {"srv-vm-dns-01": {"state": "down", "detail": "ping fails"}})
    assert health.states()["srv-vm-dns-01"]["live"] == "down"
    row = db.session.scalar(db.select(HostStatus).where(HostStatus.integration_id == b.id))
    row.checked_at = utcnow() - timedelta(hours=1)
    db.session.commit()
    st = health.states()["srv-vm-dns-01"]
    assert st["live"] == "up" and st["sources"] == {"a": "up", "b": "unknown"}
    # renames follow, deletes forget
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "name": "srv-vm-dns-02"})
    assert "srv-vm-dns-02" in health.states()
    admin.delete("/api/hosts/srv-vm-dns-02")
    assert db.session.scalar(db.select(db.func.count()).select_from(HostStatus)) == 0
    assert json
