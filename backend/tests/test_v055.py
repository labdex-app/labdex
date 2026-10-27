"""v0.5.5: several prefixes per network (IPv4 and IPv6)."""
import yaml

from labdex import discovery, inventory
from labdex.generators import aliases
from labdex.models import Network, db

from .test_v03 import put_host

NET = {"name": "lab", "vlan": 120, "zone": "DC_TC", "prefixes": [
    {"family": 4, "prefix": "10.10.120.0/24", "gateway": "254", "dhcp_start": "50", "dhcp_end": "199"},
    {"family": 4, "prefix": "10.10.121.0/25", "gateway": ".126", "description": "second v4"},
    {"family": 6, "prefix": "fd00:10:10:120::/64", "gateway": "::1", "description": "ULA"},
    {"family": 6, "prefix": "2001:db8:0:78::/64", "gateway": "::1", "description": "GUA"},
    {"family": 6, "source": "delegated", "delegated_from": "wan", "prefix_id": "0x78", "length": 64, "gateway": "::1", "description": "PD"},
]}


def test_network_with_several_prefixes(admin):
    r = admin.post("/api/networks", json=NET)
    assert r.status_code == 201, r.get_json()
    n = admin.get("/api/networks/lab").get_json()
    p = n["prefixes"]
    assert p[0] == {"family": 4, "prefix": "10.10.120.0/24", "source": "static", "gateway": "10.10.120.254",
                    "dhcp_start": "10.10.120.50", "dhcp_end": "10.10.120.199", "description": ""}
    assert p[1]["gateway"] == "10.10.121.126" and p[2]["gateway"] == "fd00:10:10:120::1"
    assert p[4]["source"] == "delegated" and p[4]["prefix_id"] == 0x78 and p[4]["gateway"] == "::1" and p[4]["prefix"] == ""
    # the old single-prefix fields mirror the first of each family
    assert (n["prefix4"], n["gateway4"], n["dhcp_start"], n["prefix6"]) == ("10.10.120.0/24", "10.10.120.254", "10.10.120.50", "fd00:10:10:120::/64")
    # aliases hold every prefix of a family
    al = {a.name: a for a in aliases.all_aliases()}
    assert al["N6_ZONE_DC_TC_LAB"].content == ["fd00:10:10:120::/64", "2001:db8:0:78::/64"]
    assert al["N4_ZONE_DC_TC_LAB"].content == ["10.10.120.0/24", "10.10.121.0/25"]
    # usage per prefix
    u = n["usage"]
    assert u[0]["free"][0] == "10.10.120.1" and u[0]["dhcp_size"] == 150 and u[2]["used"] == 1  # the gateway


def test_prefix_validation(admin):
    def errs(data):
        r = admin.post("/api/networks", json={"name": "bad", "zone": "DC_TC", **data})
        assert r.status_code == 422, r.get_json()
        return {e["field"]: e["message"] for e in r.get_json()["errors"]}
    assert "overlaps" in errs({"prefixes": [{"family": 4, "prefix": "10.10.100.0/25"}]})["prefixes[0].prefix"]  # srv
    e = errs({"prefixes": [{"family": 4, "prefix": "10.20.0.0/24"}, {"family": 4, "prefix": "10.20.0.128/25"}]})
    assert "overlaps" in e["prefixes[1].prefix"]
    assert "IPv6" in errs({"prefixes": [{"family": 6, "prefix": "10.30.0.0/24"}]})["prefixes[0].prefix"]
    assert "outside" in errs({"prefixes": [{"family": 4, "prefix": "10.30.0.0/24", "gateway": "10.31.0.1"}]})["prefixes[0].gateway"]
    assert "only IPv6" in errs({"prefixes": [{"family": 4, "source": "delegated"}]})["prefixes[0].source"]
    assert "interface ID" in errs({"prefixes": [{"family": 6, "source": "delegated", "gateway": "x"}]})["prefixes[0].gateway"]
    assert "both ends" in errs({"prefixes": [{"family": 4, "prefix": "10.30.0.0/24", "dhcp_start": "50"}]})["prefixes[0].dhcp_end"]


def test_host_addresses_in_any_prefix(admin):
    admin.post("/api/networks", json=NET)
    r = admin.post("/api/hosts", json={"name": "srv-vm-lab-01", "interfaces": [{"network": "lab", "ip4": "10.10.121.5", "ip6": "2001:db8:0:78::5"}]})
    assert r.status_code == 201, r.get_json()
    r = admin.post("/api/hosts", json={"name": "srv-vm-lab-02", "interfaces": [{"network": "lab", "ip4": "10.10.122.5"}]})
    assert r.status_code == 422 and "10.10.120.0/24, 10.10.121.0/25" in str(r.get_json())
    assert discovery._network_of("2001:db8:0:78::99").name == "lab"
    put_host(admin, "srv-vm-lab-01", interfaces=[{"network": "lab", "ip4": "10.10.121.5", "ip6": "fd00:10:10:120::5"}])


def test_prefixes_in_yaml(admin):
    admin.post("/api/networks", json=NET)
    data = yaml.safe_load(inventory.export_yaml())
    lab = next(n for n in data["networks"] if n["name"] == "lab")
    srv = next(n for n in data["networks"] if n["name"] == "srv")
    assert "prefix4" not in lab and len(lab["prefixes"]) == 5 and lab["prefixes"][4]["source"] == "delegated"
    assert "prefixes" not in srv and srv["prefix4"] == "10.10.100.0/24"  # simple networks stay short
    inventory.import_yaml(yaml.safe_dump(data))
    assert yaml.safe_load(inventory.export_yaml())["networks"] == data["networks"]


def test_migration_builds_prefixes(app):
    from alembic import command
    from sqlalchemy import text
    from labdex import schema
    from tests.conftest import EXAMPLE
    inventory.import_yaml(EXAMPLE.read_text())
    db.session.commit()
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0004_dns_zones_srv_mac")
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE network SET gateway4 = '10.10.100.254', dhcp_start = '10.10.100.200', dhcp_end = '10.10.100.220' WHERE name = 'srv'"))
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    srv = db.session.scalar(db.select(Network).where(Network.name == "srv"))
    assert [(p.family, p.prefix, p.gateway, p.dhcp_start) for p in srv.prefixes] == [
        (4, "10.10.100.0/24", "10.10.100.254", "10.10.100.200"), (6, "fd00:10:10:100::/64", "", "")]
