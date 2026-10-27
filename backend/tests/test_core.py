"""Naming rules, IPAM, generators and the YAML round trip."""
import json

import pytest
import yaml

from labdex import inventory, ipam, lint, naming, store
from labdex.generators import aliases, dns, hostfiles, opnsense
from labdex.models import Host, Network, db


def host(name):
    return db.session.scalar(db.select(Host).where(Host.name == name))


def net(name):
    return db.session.scalar(db.select(Network).where(Network.name == name))


# ------------------------------------------------------------------ naming
def test_no_zone_defaults_then_example(app):
    assert naming.all_zones() == []
    from labdex import zones as z
    z.load_example()
    db.session.flush()
    zones = naming.all_zones()
    assert zones[:2] == ["INET", "INET_UT"] and "DC_HT" in zones and "DC" in zones and len(zones) == 14
    assert naming.zone_long("DMZ_TC") == "DMZ" and naming.zone_long("DC") == "Datacenter"
    assert naming.parse_zone("XX_TC") is None and naming.parse_zone("DC_ZZ") is None
    assert naming.parse_zone("DC").default and naming.trust_rank("DC") is None and naming.trust_rank("DC_HT") == 5


def test_hostname_pattern(app):
    rx = naming.hostname_re()
    assert rx.match("net-fw-edge-01") and rx.match("srv-vm-k3s-12")
    for bad in ("arcanine", "srv-vm-k3s-1", "foo-vm-dns-01", "srv-vm-dns", "SRV-vm-dns-01"):
        assert not rx.match(bad), bad


def test_zone_colours(app):
    from labdex import zones as z
    z.load_example()
    db.session.flush()
    assert naming.zone_colour("DC_HT") == "magenta"
    assert naming.zone_colour("DMZ_TC") == "red"
    assert naming.zone_colour("DC_TN") == "yellow"
    assert naming.zone_hex("DC") == "#4ade80"  # the area's colour
    assert naming.nearest_ansi("#e000e0") == "magenta"


# -------------------------------------------------------------------- IPAM
def test_ipam_free_skips_gateway_used_and_dhcp(loaded):
    main = net("main")
    free = ipam.free(main, limit=300)
    assert "10.10.10.254" not in free          # gateway
    assert "10.10.10.200" not in free          # used by cli-pc-admin-01
    assert "10.10.10.50" not in free and "10.10.10.199" not in free  # DHCP range
    assert free[0] == "10.10.10.1"
    st = ipam.stats(main)
    assert st["size"] == 254 and st["dhcp_size"] == 150 and st["used"] == 4


def test_ipam_huge_network_not_enumerated(loaded):
    assert ipam.free(net("tailnet")) == []
    assert ipam.contains(net("internet"), "198.51.100.10") is None  # no prefix to check


# ------------------------------------------------------------- validation
def test_ip_outside_network_rejected(loaded):
    with pytest.raises(store.ValidationError) as exc:
        store.save_host({"name": "srv-vm-x-01", "zone": "DC_TC",
                         "interfaces": [{"network": "srv", "ip4": "10.10.10.5"}]})
    assert "outside srv" in exc.value.errors[0]["message"]
    db.session.rollback()


def test_fun_name_must_be_unique_against_hostnames_too(loaded):
    with pytest.raises(store.ValidationError):
        store.save_host({"name": "srv-vm-y-01", "zone": "DC_TC", "fun": "arcanine"})
    db.session.rollback()
    with pytest.raises(store.ValidationError):
        store.save_host({"name": "srv-vm-y-01", "zone": "DC_TC", "fun": "net-fw-edge-01"})
    db.session.rollback()


def test_mac_normalised(loaded):
    h = store.save_host({"name": "srv-vm-z-01", "zone": "DC_TC",
                         "interfaces": [{"network": "srv", "ip4": "10.10.100.99", "mac": "AA-BB-CC-DD-EE-FF"}]})
    assert h.interfaces[0].mac == "aa:bb:cc:dd:ee:ff"
    db.session.rollback()


def test_alias_names_and_firewall_ids(loaded):
    from labdex.generators import aliases as galiases
    for bad in ({"name": ""}, {"name": "x" * 129}, {"name": "ok", "fw_id": "has space"}, {"name": "ok", "fw_id": "X" * 33}):
        with pytest.raises(store.ValidationError):
            store.save_alias({"type": "host", **bad})
        db.session.rollback()
    store.save_alias({"name": "Web ports for the reverse proxy (public)", "type": "port", "members": "80,443"})
    store.save_alias({"name": "Admin hosts", "fw_id": "HG_ADMINS", "type": "host", "members": ["host:sonos-living", "alias:Web ports for the reverse proxy (public)"]})
    db.session.flush()
    from labdex import cache
    cache.bump()
    al = {a.name: a for a in galiases.all_aliases()}
    web = al["Web ports for the reverse proxy (public)"]
    assert len(web.fw_id) <= 32 and web.fw_id.startswith("WEB_PORTS_FOR_THE_REVERSE")
    assert al["Admin hosts"].fw_id == "HG_ADMINS"
    assert web.fw_id in al["Admin hosts"].content  # members use firewall ids
    long_host = naming.firewall_id("H6_ZONE_DMZ_TC_SRV_VM_REVERSE_PROXY_PUBLIC_01")
    assert len(long_host) == 32 and long_host == naming.firewall_id("H6_ZONE_DMZ_TC_SRV_VM_REVERSE_PROXY_PUBLIC_01")
    db.session.rollback()


def test_network_validation(loaded):
    with pytest.raises(store.ValidationError) as exc:
        store.save_network({"name": "x", "vlan": 10, "zone": "CA_ST", "prefix4": "10.1.0.1/24",
                            "dhcp_start": "10.2.0.5"})
    fields = {e["field"] for e in exc.value.errors}
    assert {"vlan", "prefix4", "dhcp_end"} <= fields
    db.session.rollback()


# -------------------------------------------------------------- generators
def test_generated_aliases(loaded):
    names = {a.name: a for a in aliases.all_aliases()}
    assert names["N_ZONE_DC_HT_MGMT"].content == ["N4_ZONE_DC_HT_MGMT", "N6_ZONE_DC_HT_MGMT"]
    assert names["NG_ZONE_DC"].content == ["NG_ZONE_DC_HT", "NG_ZONE_DC_TC"]
    assert names["H4_ZONE_DC_HT_NET_FW_EDGE_01"].content[0] == "10.10.3.253"  # numeric sort
    assert names["MG_ZONE_CA_UT_SONOS"].content == ["00:1a:2b:00:20:01"]
    assert names["NG_SPECIAL_INTERNAL"].content == ["NG_ZONE_CA", "NG_ZONE_RA", "NG_ZONE_DC", "NG_ZONE_DMZ"]
    assert all(len(a.name) <= 32 for a in names.values())


def test_offline_hosts_get_no_aliases(loaded):
    host("srv-vm-mon-01").status = "offline"
    names = {a.name for a in aliases.all_aliases()}
    assert "H_ZONE_DC_TC_SRV_VM_MON_01" not in names


def test_unresolvable_alias_member_is_reported(loaded):
    store.save_alias({"name": "HG_SPECIAL_BROKEN", "type": "host", "members": ["host:nope", "alias:NOPE"]})
    a = next(a for a in aliases.all_aliases() if a.name == "HG_SPECIAL_BROKEN")
    assert len(a.errors) == 2
    assert any(i["code"] == "alias-member" for i in lint.run())
    db.session.rollback()


def test_opnsense_export_orders_dependencies_and_is_stable(loaded):
    data = opnsense.export()["aliases"]["alias"]
    order = [v["name"] for v in data.values()]
    for v in data.values():
        for member in v["content"].split("\n"):
            if member in order:
                assert order.index(member) < order.index(v["name"]), (member, v["name"])
    assert opnsense.export() == opnsense.export()
    assert all("[labdex]" in v["description"] for v in data.values())


def test_dns_records(loaded):
    recs = {(r.name, r.type): r.value for r in dns.records()}
    assert recs[("net-fw-edge-01.mgmt", "A")] == "10.10.3.253"
    assert recs[("arcanine", "CNAME")] == "net-fw-edge-01.mgmt.lab.example.net."
    assert recs[("srv-vm-dns-01.srv", "AAAA")] == "fd00:10:10:100::53"
    assert recs[("_grafana._tcp", "SRV")] == "0 0 443 srv-vm-mon-01.srv.lab.example.net."
    assert recs[("grafana", "CNAME")] == "srv-vm-mon-01.srv.lab.example.net."
    # two NICs in one network get labelled names
    assert ("cli-nb-admin-01-wifi.main", "A") in recs
    # networks with in_dns=false produce nothing
    assert not any(n.endswith(".storage") for n, _ in recs)
    assert "$ORIGIN lab.example.net." in dns.bind()


def test_ansible_inventory(loaded):
    inv = hostfiles.ansible_inventory()["all"]
    fw = inv["hosts"]["net-fw-edge-01"]
    assert fw["ansible_host"] == "10.10.3.253" and fw["labdex_fun"] == "arcanine"
    assert fw["labdex_prompt_colour"] == "magenta"
    assert "net-fw-edge-01" in inv["children"]["zone_dc_ht"]["hosts"]
    assert "cli-pc-admin-01" in inv["children"]["tag_admin"]["hosts"]
    assert "sonos-living" in inv["children"]["cat_unnamed"]["hosts"]
    yaml.safe_load(hostfiles.ansible_yaml())


def test_motd(loaded):
    text = hostfiles.motd(host("dmz-vm-docker-01"))
    assert "aegislash  ·  dmz-vm-docker-01" in text
    assert "internet-facing" in text and "Runs on   srv-pm-pve-02" in text


def test_dhcp_reservations(loaded):
    rows = hostfiles.dhcp_reservations()
    assert [r["ip"] for r in rows][:2] == ["10.10.3.2", "10.10.10.200"]
    assert "mac,ip" in hostfiles.dhcp_csv().splitlines()[0]


# -------------------------------------------------------------------- lint
def test_example_is_clean(loaded):
    s = lint.summary(lint.run())
    assert s["error"] == 0 and s["warning"] == 0, lint.run()


def test_lint_catches_conventions(loaded):
    h = host("srv-vm-k3s-02")
    h.interfaces[0].ip4 = "10.10.128.11"          # duplicate of k3s-01
    store.save_host({"name": "printer", "zone": "CA_UT",   # bad name, UT in an HT network
                     "interfaces": [{"network": "mgmt", "ip4": "10.10.3.77"}]})
    net("k8s").vlan = 129                           # octet mismatch
    codes = {i["code"] for i in lint.run()}
    assert {"duplicate-ip", "hostname", "placement", "octet-vlan"} <= codes
    db.session.rollback()


# ---------------------------------------------------------------- inventory
def test_yaml_round_trip(loaded):
    first = inventory.export_yaml()
    inventory.import_yaml(first)
    assert inventory.export_yaml() == first


def test_bad_import_changes_nothing(loaded):
    before = inventory.export_yaml()
    bad = yaml.safe_load(before)
    bad["hosts"][0]["interfaces"][0]["ip4"] = "not-an-ip"
    bad["networks"].append({"name": "dup", "vlan": 3, "zone": "DC_HT"})
    with pytest.raises(store.ValidationError) as exc:
        inventory.import_dict(bad)
    assert len(exc.value.errors) >= 2
    assert inventory.export_yaml() == before


def test_import_rejects_garbage(app):
    with pytest.raises(store.ValidationError):
        inventory.import_yaml("- just\n- a list\n")
    with pytest.raises(store.ValidationError):
        inventory.import_yaml("hosts: [{name: x}]\nnetworks: {broken")


def test_runs_on_may_point_forward(app):
    inventory.import_dict({"areas": [{"code": "DC", "name": "Datacenter"}],
                           "trust_levels": [{"code": "TC", "rank": 4}, {"code": "HT", "rank": 5}], "hosts": [
        {"name": "srv-vm-a-01", "zone": "DC_TC", "runs_on": "srv-pm-b-01"},
        {"name": "srv-pm-b-01", "zone": "DC_HT"},
    ]})
    assert host("srv-vm-a-01").runs_on.name == "srv-pm-b-01"


def test_json_exports_parse(loaded):
    json.loads(dns.as_json())
    json.loads(opnsense.export_json())
