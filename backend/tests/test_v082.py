"""v0.8.2: Tailscale / Headscale."""
import pytest

from labdex.models import DiscoveredHost, HostStatus, db

from .fakes import FakeServer, FakeTailnet
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


@pytest.fixture
def tailnet():
    fake = FakeTailnet()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.devices = [
        {"name": "admin-nb.tail1234.ts.net", "hostname": "admin-nb", "addresses": ["100.64.0.10", "fd7a:115c:a1e0::a"], "os": "linux",
         "connectedToControl": True, "lastSeen": "2026-10-24T10:00:00Z", "clientVersion": "1.76.1-t123", "keyExpiryDisabled": True},
        {"name": "phone.tail1234.ts.net", "hostname": "phone", "addresses": ["100.64.0.11"], "os": "android",
         "connectedToControl": False, "lastSeen": "2026-10-20T10:00:00Z"},
    ]
    yield fake
    srv.close()


@pytest.mark.parametrize("flavor", ["tailscale", "headscale"])
def test_devices(admin, tailnet, flavor):
    # the admin notebook already has its tailnet address in the inventory
    admin.post("/api/networks", json={"name": "tailnet", "zone": "RA_ST", "prefixes": [{"family": 4, "prefix": "100.64.0.0/10"}]})
    h = host(admin, "cli-nb-admin-01")
    put_host(admin, "cli-nb-admin-01", interfaces=h["interfaces"] + [{"network": "tailnet", "ip4": "100.64.0.10"}])
    make_instance(admin, "tailscale", "ts", {"flavor": flavor, "url": tailnet.url, "api_key": "tskey"})
    assert run(admin, "ts", "test")["result"]["message"] == "2 devices, 1 online"
    job = run(admin, "ts", "poll")
    assert job["result"]["matched"] == 1 and job["result"]["online"] == 1, job
    st = db.session.scalar(db.select(HostStatus))
    assert st.host == "cli-nb-admin-01" and st.state == "up"
    seen = {d.ip: d for d in db.session.scalars(db.select(DiscoveredHost))}
    assert seen["100.64.0.11"].hostname == "phone" and not seen["100.64.0.11"].up and seen["100.64.0.10"].up
    # the phone is an unknown host in Discovery, in the tailnet network
    unknown = admin.get("/api/discovery/unknown-hosts").get_json()["items"]
    assert any(u["ip"] == "100.64.0.11" and u["network"] == "tailnet" for u in unknown)
