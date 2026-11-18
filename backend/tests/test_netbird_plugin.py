"""The NetBird plugin: peers into Discovery and host status, read-only."""
from pathlib import Path

import pytest

from labdex.testing import FakeServer, PluginHarness

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "netbird"

INVENTORY = {
    "zones": [{"code": "RA_ST", "name": "Remote access", "rank": 2}, {"code": "DC_TC", "name": "Servers", "rank": 4}],
    "areas": [{"code": "RA", "name": "Remote access"}, {"code": "DC", "name": "Datacenter"}],
    "networks": [{"name": "mesh", "zone": "RA_ST", "prefix4": "100.64.0.0/16"}],
    "hosts": [{"name": "srv-vm-web-01", "zone": "DC_TC", "interfaces": [{"network": "mesh", "ip4": "100.64.0.10"}]},
              {"name": "srv-vm-db-01", "zone": "DC_TC"}],
}

PEERS = [
    {"id": "p1", "name": "web", "hostname": "srv-vm-web-01", "ip": "100.64.0.10", "dns_label": "web.netbird.example",
     "connected": True, "last_seen": "2026-01-01T00:00:00Z", "os": "Ubuntu 24.04", "version": "0.60.0",
     "groups": [{"id": "g1", "name": "All"}, {"id": "g2", "name": "servers"}], "login_expired": False},
    {"id": "p2", "name": "db", "hostname": "srv-vm-db-01", "ip": "100.64.0.11", "dns_label": "srv-vm-db-01.netbird.example",
     "connected": False, "last_seen": "2026-01-01T00:00:00Z", "os": "Debian 12", "version": "0.60.0",
     "groups": [{"id": "g2", "name": "servers"}], "login_expired": False},
    {"id": "p3", "name": "phone", "hostname": "phone", "ip": "100.64.0.20", "dns_label": "phone.netbird.example",
     "connected": True, "last_seen": "", "os": "iOS", "version": "0.60.0", "groups": [{"id": "g1", "name": "All"}]},
]


@pytest.fixture
def api():
    with FakeServer() as s:
        s.route("GET", "/api/peers", json=PEERS)
        yield s


def _harness(api, **settings):
    return PluginHarness(PLUGIN, settings={"url": api.url, "token": "nbp_example", **settings}, inventory=INVENTORY)


def test_peers_and_token(api):
    with _harness(api) as h:
        peers = h.plugin.peers()
        assert [p["name"] for p in peers] == ["web", "db", "phone"]
        assert peers[0]["addresses"] == ["100.64.0.10"] and peers[0]["groups"] == ["All", "servers"]
        assert api.calls("GET", "/api/peers")[0].headers["Authorization"] == "Token nbp_example"
        assert h.run("test") == {"message": "3 peers, 2 connected"}
        assert all(c.method == "GET" for c in api.requests)  # read-only


def test_group_filter(api):
    with _harness(api, groups=["Servers"]) as h:
        assert [p["name"] for p in h.plugin.peers()] == ["web", "db"]


def test_poll_reports_status_and_fills_discovery(api, monkeypatch):
    with _harness(api, offline="down") as h:
        reported = {}
        monkeypatch.setattr(h.ctx, "report_status", lambda r: reported.update(r) or {"changes": []})
        out = h.run("poll")
        assert out["peers"] == 3 and out["connected"] == 2 and out["matched"] == 2
        assert reported["srv-vm-web-01"]["state"] == "up"
        assert reported["srv-vm-db-01"]["state"] == "down"  # matched by hostname, no mesh address in labdex
        from labdex.models import DiscoveredHost, db
        seen = {d.ip: d.up for d in db.session.scalars(db.select(DiscoveredHost))}
        assert seen.get("100.64.0.20") and seen.get("100.64.0.11") is False
        host = h.widget("host", "srv-vm-web-01")
        assert {"label": "groups", "value": "All, servers"} in host["items"]
        dash = h.widget("peers")
        assert dash["sub"] == "2 of 3 connected" and any("not in the inventory" in i["detail"] for i in dash["items"])


def test_missing_token_and_wrong_api(api):
    from labdex.plugins.base import PluginError
    with PluginHarness(PLUGIN, settings={"url": api.url}, inventory=INVENTORY) as h:
        with pytest.raises(PluginError, match="token"):
            h.plugin.peers()
    with FakeServer() as other:
        other.route("GET", "/api/peers", json={"message": "nope"})
        with _harness(other) as h, pytest.raises(PluginError, match="unexpected"):
            h.plugin.peers()
