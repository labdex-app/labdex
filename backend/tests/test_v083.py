"""v0.8.3: UniFi Network."""
from labdex.models import DiscoveredHost, HostStatus, db

from .fakes import FakeServer, FakeUniFi
from .test_plugins import make_instance, run


def test_unifi_clients_and_devices(admin):
    fake = FakeUniFi()
    srv = FakeServer(fake)
    try:
        fake.clients = [{"type": "WIRELESS", "id": f"c{i}", "name": f"phone-{i}", "ipAddress": f"172.16.{i // 200}.{i % 200 + 1}",
                         "macAddress": f"AA:BB:CC:00:01:{i:02X}"} for i in range(250)] + [{"type": "WIRED", "id": "x", "name": "nothing"}]
        fake.devices = [{"id": "d1", "name": "core", "model": "USW-Pro-24", "macAddress": "aa:aa:aa:aa:aa:01", "ipAddress": "10.10.3.2", "state": "ONLINE"},
                        {"id": "d2", "name": "ap-attic", "model": "U7-Pro", "macAddress": "aa:aa:aa:aa:aa:02", "ipAddress": "10.10.3.40", "state": "OFFLINE"}]
        make_instance(admin, "unifi", "unifi", {"url": srv.url, "api_key": "uk"})
        assert run(admin, "unifi", "test")["result"]["message"] == "1 site(s): Default"
        job = run(admin, "unifi", "poll")
        assert job["result"] == {"clients": 251, "recorded": 250, "devices": 2, "matched": 1}, job
        d = db.session.scalar(db.select(DiscoveredHost).where(DiscoveredHost.ip == "172.16.0.1"))
        assert d.mac == "aa:bb:cc:00:01:00" and d.hostname == "phone-0"
        st = db.session.scalar(db.select(HostStatus))
        assert st.host == "net-sw-core-01" and st.state == "up" and "USW-Pro-24" in st.detail
    finally:
        srv.close()
