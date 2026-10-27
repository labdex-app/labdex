"""v0.5.7: delegated IPv6 prefixes read from OPNsense."""
from labdex import prefixes
from labdex.generators import aliases, dns

from .fakes import FakeOPNsense
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


def test_derive():
    assert prefixes.derive("2001:db8:ab00::/56", 0x64, 64) == "2001:db8:ab00:64::/64"
    assert prefixes.derive("2001:db8:ab00::/56", 0, 64) == "2001:db8:ab00::/64"
    assert prefixes.derive("2001:db8:ab00::/56", 0x100, 64) is None  # only 256 /64s in a /56
    assert prefixes.derive("2001:db8:ab00::/56", 1, 48) is None
    assert prefixes.derive("nonsense", 1, 64) is None


def test_sync_prefixes_from_opnsense(admin):
    n = admin.get("/api/networks/srv").get_json()
    n["prefixes"].append({"family": 6, "source": "delegated", "delegated_from": "wan", "prefix_id": "0x64", "length": 64,
                          "gateway": "::1", "description": "GUA"})
    assert admin.put("/api/networks/srv", json=n).status_code == 200
    h = host(admin, "srv-vm-dns-01")
    h["interfaces"][0]["ip6_token"] = "::53"
    put_host(admin, "srv-vm-dns-01", interfaces=h["interfaces"])
    assert host(admin, "srv-vm-dns-01")["interfaces"][0]["ip6_all"] == ["fd00:10:10:100::53"]  # GUA not known yet

    fw = FakeOPNsense()
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        job = run(admin, "fw", "sync_prefixes")
        assert job["status"] == "succeeded" and job["result"] == {"changed": 1, "unchanged": 0, "problems": 0}, job
        assert next(p for p in admin.get("/api/networks/srv").get_json()["prefixes"] if p["source"] == "delegated")["prefix"] == "2001:db8:ab00:64::/64"
        assert "2001:db8:ab00:64::53" in host(admin, "srv-vm-dns-01")["interfaces"][0]["ip6_all"]
        assert any(r.value == "2001:db8:ab00:64::53" for r in dns.records() if r.type == "AAAA")
        n6 = next(a for a in aliases.all_aliases() if a.name == "N6_ZONE_DC_TC_SRV")
        assert "2001:db8:ab00:64::/64" in n6.content
        ev = admin.get("/api/history?object_type=network&object_name=srv").get_json()[0]
        assert "unknown → 2001:db8:ab00:64::/64" in ev["summary"] and "from fw" in ev["summary"]
        assert run(admin, "fw", "sync_prefixes")["result"]["unchanged"] == 1
        # the ISP hands out a new prefix: everything follows
        fw.interfaces[0]["ifctl.prefix"] = ["2001:db8:cd00::/56"]
        assert run(admin, "fw", "sync_prefixes")["result"]["changed"] == 1
        assert "2001:db8:cd00:64::53" in host(admin, "srv-vm-dns-01")["interfaces"][0]["ip6_all"]
        assert "2001:db8:ab00:64::53" not in host(admin, "srv-vm-dns-01")["interfaces"][0]["ip6_all"]
        # nothing on the named interface
        n = admin.get("/api/networks/srv").get_json()
        next(p for p in n["prefixes"] if p["source"] == "delegated")["delegated_from"] = "opt7"
        admin.put("/api/networks/srv", json=n)
        job = run(admin, "fw", "sync_prefixes")
        assert job["result"]["problems"] == 1 and "nothing received on 'opt7'" in admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    finally:
        fw.srv.close()
