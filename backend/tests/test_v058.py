"""v0.5.8: IPv6 discovery: NDP, DHCPv6 leases, nmap on known IPv6 addresses."""
import subprocess

from labdex import discovery
from labdex.models import DiscoveredHost, db

from .fakes import FakeOPNsense
from .test_plugins import SAMPLE_XML, make_instance, run


def test_ndp_and_dhcpv6_leases(admin):
    fw = FakeOPNsense()
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        job = run(admin, "fw", "import_arp")
        assert job["status"] == "succeeded", job
        ips = {d.ip: d for d in db.session.scalars(db.select(DiscoveredHost))}
        assert "fd00:10:10:10::77" in ips and ips["fd00:10:10:10::77"].mac == "aa:bb:cc:00:00:77"
        assert not any(ip.startswith("fe80") for ip in ips)  # link-local says nothing about the network
        job = run(admin, "fw", "import_leases")
        assert job["result"] == {"leases": 2, "leases6": 1}, job  # the IA_PD is a delegated prefix, not an address
        u = {x["ip"]: x for x in admin.get("/api/discovery/unknown-hosts").get_json()["items"]}
        assert u["fd00:10:10:10::1234"]["hostname"] == "laptop" and u["fd00:10:10:10::1234"]["network"] == "main"
    finally:
        fw.srv.close()


def test_nmap_scans_known_ipv6_addresses(admin, monkeypatch):
    discovery.record_neighbours("ndp", [{"ip": "fd00:10:10:100::99", "mac": "aa:bb:cc:00:00:99"},
                                        {"ip": "fd00:99::1", "mac": "aa:bb:cc:00:00:98"}])  # outside every chosen prefix
    db.session.commit()
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            path = cmd[cmd.index("-oX") + 1]
            with open(path, "w") as f:
                f.write(SAMPLE_XML if "-6" not in cmd else "<nmaprun/>")
            self.stdout = iter(["Discovered open port 53/udp on 10.10.100.53\n", "Nmap done: 1 IP address\n"])

        def wait(self):
            return 0

        def kill(self):
            pass
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/nmap")
    make_instance(admin, "nmap", "scan", {"networks": ["srv"], "exclude": ["fd00:10:10:100::20"]})
    job = run(admin, "scan", "scan")
    log = admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    assert job["status"] == "succeeded", log
    assert "Discovered open port 53/udp" in log and "10.10.100.53" in log  # streamed, and a summary per host
    v4, v6, *declared = calls
    assert declared and all("-p" in c and "--top-ports" not in c for c in declared)  # the declared services' ports, on purpose
    assert "10.10.100.0/24" in v4 and "-6" not in v4
    targets = [a for a in v6 if a.startswith("fd00")]
    assert "fd00:10:10:100::53" in targets and "fd00:10:10:100::99" in targets  # inventory + discovered
    assert "fd00:10:10:100::20" not in targets and "fd00:99::1" not in targets   # excluded, not in a chosen network
    # turned off: no second run
    calls.clear()
    admin.put("/api/integrations/scan", json={"settings": {"networks": ["srv"], "ipv6": False, "service_ports": False}})
    run(admin, "scan", "scan")
    assert len(calls) == 1
