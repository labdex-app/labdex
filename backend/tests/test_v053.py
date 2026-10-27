"""v0.5.3: DNS zones per network and host, SRV opt-in, Technitium with several zones, MACs from discovery."""
from labdex import discovery
from labdex.generators import dns
from labdex.models import Host, db

from .fakes import FakeTechnitium
from .test_plugins import make_instance, run
from .test_v03 import host, put_host

D = "lab.example.net"


def set_net(admin, name, **kw):
    n = admin.get(f"/api/networks/{name}").get_json()
    r = admin.put(f"/api/networks/{name}", json={**n, **kw})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def fqdns():
    return {(r.fqdn(D), r.type): r.value for r in dns.records()}


# --------------------------------------------------------------------- zones
def test_network_zone_override(admin):
    assert ("sonos-living.iot." + D, "A") in fqdns()
    n = set_net(admin, "iot", dns_zone="home.lab.")
    assert n["dns_zone"] == "home.lab" and n["dns_suffix"] == "home.lab"
    recs = fqdns()
    assert recs[("sonos-living.home.lab", "A")].startswith("10.10.201.")
    assert recs[("sonos-living." + D, "CNAME")] == "sonos-living.home.lab."  # the short name stays in the domain
    assert dns.zones() == [D, "home.lab"]
    assert "$ORIGIN home.lab." in dns.bind() and f"$ORIGIN {D}." in dns.bind()
    # '@': straight into the domain; the short name is then the A record itself, no CNAME beside it
    set_net(admin, "iot", dns_zone="@")
    recs = fqdns()
    assert ("sonos-living." + D, "A") in recs and ("sonos-living." + D, "CNAME") not in recs
    r = admin.put("/api/networks/iot", json={**admin.get("/api/networks/iot").get_json(), "dns_zone": "not a zone"})
    assert r.status_code == 422 and "dns_zone" in str(r.get_json())


def test_two_networks_into_one_zone_get_distinct_names(admin):
    set_net(admin, "mgmt", dns_zone="@")
    set_net(admin, "srv", dns_zone="@")
    recs = fqdns()
    # the edge firewall has an address in both: the second network's record carries the network name
    assert ("net-fw-edge-01." + D, "A") in recs and ("net-fw-edge-01-srv." + D, "A") in recs


def test_host_zone_override(admin):
    h = put_host(admin, "srv-vm-dns-01", dns_zone="home.lab", fun_names=["unown"],
                 records=[{"name": "ns1", "type": "CNAME", "value": ""}])
    recs = {(r["fqdn"], r["type"]): r["value"] for r in h["dns"]}
    assert recs[("srv-vm-dns-01.home.lab", "CNAME")] == f"srv-vm-dns-01.srv.{D}."
    assert ("unown.home.lab", "CNAME") in recs and ("ns1.home.lab", "CNAME") in recs
    assert ("srv-vm-dns-01.srv." + D, "A") in recs  # address records follow the network
    assert any(link["url"].startswith("http://dns.home.lab") for link in h["links"])
    r = admin.put("/api/hosts/srv-vm-dns-01", json={**host(admin, "srv-vm-dns-01"), "dns_zone": "bad zone!"})
    assert r.status_code == 422


def test_srv_is_opt_in(admin):
    recs = fqdns()
    assert ("_grafana._tcp." + D, "SRV") in recs                 # the example opts in
    assert ("_dns-admin._tcp." + D, "SRV") not in recs           # this one doesn't
    h = host(admin, "srv-vm-dns-01")
    for s in h["services"]:
        s["publish_srv"] = s["name"] == "dns-admin"
    put_host(admin, "srv-vm-dns-01", services=h["services"])
    recs = fqdns()
    assert ("_dns-admin._tcp." + D, "SRV") in recs and ("_dns-admin._tcp." + D, "TXT") in recs
    assert ("_dns._udp." + D, "SRV") not in recs


def test_migration_keeps_srv_for_existing_services(app):
    from alembic import command
    from labdex import inventory, schema
    from labdex.models import Service
    from tests.conftest import EXAMPLE
    inventory.import_yaml(EXAMPLE.read_text())
    db.session.commit()
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0003_function_dns_pools")
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    assert all(s.publish_srv for s in db.session.scalars(db.select(Service)))


# ---------------------------------------------------------------- Technitium
def test_technitium_several_zones(admin):
    t = FakeTechnitium(D, zones=[D, "home.lab"])
    try:
        set_net(admin, "iot", dns_zone="home.lab")
        set_net(admin, "guest", dns_zone="guest.example.org")  # no such zone on the server
        admin.put("/api/hosts/cli-pc-admin-01", json={**host(admin, "cli-pc-admin-01"),
                  "interfaces": [{"network": "guest", "ip4": "10.10.231.60", "primary": True}]})
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": "t0ken", "verify_tls": False, "extra_zones": "legacy.lan"})
        plan = run(admin, "dns", "sync_records")
        names = {c["name"] for c in plan["plan"]}
        assert "sonos-living.home.lab A" in names and f"srv-vm-dns-01.srv.{D} A" in names
        assert not any("guest.example.org" in n for n in names)
        log = admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
        assert "no zone on the server for: guest.example.org" in log and "zones: home.lab, lab.example.net" in log
        assert admin.post(f"/api/jobs/{plan['id']}/apply").get_json()["status"] == "applied"
        assert any(r["name"] == "sonos-living.home.lab" and r["comments"] == "labdex" for r in t.records)
        assert run(admin, "dns", "sync_records")["plan"] == []  # in sync across both zones
        # the panel reads every zone
        view = admin.get("/api/hosts/sonos-living/modules/dns/view").get_json()
        assert "home.lab" in view["sections"][0]["title"]
    finally:
        t.srv.close()


def test_technitium_without_zone_listing(admin):
    t = FakeTechnitium(D, can_list=False)
    try:
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": "t0ken", "verify_tls": False})
        plan = run(admin, "dns", "sync_records")
        assert plan["status"] == "planned" and plan["plan"]
        assert "can't list zones" in admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    finally:
        t.srv.close()


# ---------------------------------------------------------------------- MACs
def test_macs_from_scan_arp_and_leases(admin):
    # scans don't learn MACs (0.10): the firewall's ARP table and DHCP leases do
    discovery.record_scan("nmap:scan", {"10.10.100.53", "10.10.100.20"}, [
        {"ip": "10.10.100.53", "services": []},
        {"ip": "10.10.100.20", "services": [], "mac": "52:54:00:00:00:99", "vendor": "QEMU"}])
    discovery.record_neighbours("opnsense:fw", [{"ip": "10.10.100.53", "mac": "52:54:00:00:00:53"},
                                                {"ip": "10.10.100.20", "mac": "52:54:00:00:00:20", "vendor": "QEMU"}])
    discovery.record_neighbours("kea:fw", [{"ip": "10.10.128.11", "mac": "AA:BB:CC:00:01:11"}])
    db.session.commit()
    s = admin.get("/api/discovery/summary").get_json()
    rows = {m["ip"]: m for m in admin.get("/api/discovery/macs").get_json()["items"]}
    assert rows["10.10.100.53"]["mac"] == "52:54:00:00:00:53" and rows["10.10.100.53"]["source"] == "opnsense:fw"
    assert rows["10.10.128.11"]["source"] == "kea:fw" and s["macs"] == len(rows)
    r = admin.post("/api/discovery/macs/apply", json={"items": [{"host": "srv-vm-dns-01", "ip": "10.10.100.53", "mac": "52:54:00:00:00:53"}]})
    assert r.get_json() == {"applied": 1}
    i = next(i for i in host(admin, "srv-vm-dns-01")["interfaces"] if i["ip4"] == "10.10.100.53")
    assert i["mac"] == "52:54:00:00:00:53"
    assert "MAC from discovery" in admin.get("/api/history?object_name=srv-vm-dns-01").get_json()[0]["summary"]
    assert "10.10.100.53" not in {m["ip"] for m in admin.get("/api/discovery/macs").get_json()["items"]}
    # services list shows the MAC of the address
    discovery.record_scan("nmap:scan", {"10.10.100.20"}, [{"ip": "10.10.100.20", "services": [{"port": 22, "proto": "tcp", "service": "ssh"}]}])
    db.session.commit()
    row = next(x for x in admin.get("/api/discovery/services?status=all").get_json()["items"] if x["ip"] == "10.10.100.20")
    assert row["mac"] == "52:54:00:00:00:20" and row["vendor"] == "QEMU"


def test_vip_addresses_are_not_offered_macs(admin):
    admin.post("/api/hosts", json={"name": "net-vip-k3s-01", "kind": "vip", "vip_members": ["srv-vm-k3s-01"],
                                   "interfaces": [{"network": "k8s", "ip4": "10.10.128.10"}]})
    discovery.record_neighbours("arp", [{"ip": "10.10.128.10", "mac": "aa:bb:cc:00:00:10"}])
    db.session.commit()
    assert "10.10.128.10" not in {m["ip"] for m in admin.get("/api/discovery/macs").get_json()["items"]}
    assert db.session.scalar(db.select(Host).where(Host.name == "net-vip-k3s-01")).interfaces[0].mac == ""
