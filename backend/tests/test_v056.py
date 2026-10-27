"""v0.5.6: IPv6 interface IDs used in every IPv6 prefix of the network, overrides, EUI-64, IPv6 usage."""
import yaml

from labdex import inventory
from labdex.generators import aliases, dns

from .test_v03 import host, put_host

D = "lab.example.net"


def add_gua(admin):
    n = admin.get("/api/networks/srv").get_json()
    n["prefixes"].append({"family": 6, "prefix": "2001:db8:64::/64", "description": "GUA"})
    r = admin.put("/api/networks/srv", json=n)
    assert r.status_code == 200, r.get_json()


def iface(h, ip4):
    return next(i for i in h["interfaces"] if i["ip4"] == ip4)


def test_token_applies_to_every_prefix(admin):
    add_gua(admin)
    h = host(admin, "srv-vm-dns-01")
    i = iface(h, "10.10.100.53")
    assert i["ip6_all"] == ["fd00:10:10:100::53"]  # the explicit address stands for its own prefix
    i["ip6_token"] = "53"  # normalised to ::53
    h = put_host(admin, "srv-vm-dns-01", interfaces=h["interfaces"])
    i = iface(h, "10.10.100.53")
    assert i["ip6_token"] == "::53" and i["ip6_all"] == ["fd00:10:10:100::53", "2001:db8:64::53"]
    aaaa = sorted(r.value for r in dns.records() if r.host == "srv-vm-dns-01" and r.type == "AAAA")
    assert aaaa == ["2001:db8:64::53", "fd00:10:10:100::53"]
    h6 = next(a for a in aliases.all_aliases() if a.name == "H6_ZONE_DC_TC_SRV_VM_DNS_01")
    assert h6.content == ["2001:db8:64::53", "fd00:10:10:100::53"]
    # an override for one prefix
    i["ip6_overrides"] = {"2001:db8:64::/64": "2001:db8:64::5353"}
    h = put_host(admin, "srv-vm-dns-01", interfaces=h["interfaces"])
    assert iface(h, "10.10.100.53")["ip6_all"] == ["fd00:10:10:100::53", "2001:db8:64::5353"]
    # YAML keeps token and overrides, not the computed list
    y = yaml.safe_load(inventory.export_yaml())
    yi = next(x for x in y["hosts"] if x["name"] == "srv-vm-dns-01")["interfaces"][0]
    assert yi["ip6_token"] == "::53" and yi["ip6_overrides"] == {"2001:db8:64::/64": "2001:db8:64::5353"} and "ip6_all" not in yi


def test_eui64_and_validation(admin):
    add_gua(admin)
    h = host(admin, "srv-vm-mon-01")
    h["interfaces"][0].update(mac="52:54:00:12:34:56", ip6_token="eui64")
    h = put_host(admin, "srv-vm-mon-01", interfaces=h["interfaces"])
    assert "2001:db8:64:0:5054:ff:fe12:3456" in h["interfaces"][0]["ip6_all"]

    def bad(**kw):
        h = host(admin, "srv-vm-mon-01")
        h["interfaces"][0].update(kw)
        r = admin.put("/api/hosts/srv-vm-mon-01", json=h)
        assert r.status_code == 422, r.get_json()
        return " ".join(e["field"] + ": " + e["message"] for e in r.get_json()["errors"])
    assert "ip6_token" in bad(ip6_token="eui64", mac="")
    assert "ip6_token" in bad(ip6_token="nope")
    assert "outside" in bad(ip6_overrides={"2001:db8:64::/64": "2001:db8:99::1"})
    assert "not an IPv6 prefix" in bad(ip6_overrides={"2001:db8:77::/64": "2001:db8:77::1"})


def test_ipv6_usage_next_free_and_preview(admin):
    n = admin.get("/api/networks/srv").get_json()
    u6 = next(u for u in n["usage"] if u["family"] == 6)
    assert u6["used"] >= 1 and u6["free_tokens"][0] == "::1"
    nxt = admin.get("/api/networks/srv/next").get_json()
    assert nxt["ip6_token"] == "::1"
    p = admin.post("/api/preview/host", json={"name": "x", "interfaces": [{"network": "srv", "ip6_token": "::99"}]}).get_json()
    assert p["ip6"] == [[{"address": "fd00:10:10:100::99", "prefix": "fd00:10:10:100::/64"}]]


def test_duplicate_ipv6_from_tokens_is_flagged(admin):
    h = host(admin, "srv-vm-ha-01")
    h["interfaces"][0]["ip6_token"] = "::53"  # srv-vm-dns-01 has fd00:10:10:100::53 explicitly
    put_host(admin, "srv-vm-ha-01", interfaces=h["interfaces"])
    issues = [i for i in admin.get("/api/lint").get_json()["issues"] if i["code"] == "duplicate-ip"]
    assert any("fd00:10:10:100::53" in i["message"] for i in issues)
