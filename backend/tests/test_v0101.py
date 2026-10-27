"""0.10.1: zones made one by one, area default zones, colours."""
import yaml

from labdex import inventory, lint, naming
from labdex.models import Zone, db

from .test_v03 import host, put_host


def test_migration_from_trust_levels(app):
    from alembic import command
    from sqlalchemy import text
    from labdex import schema
    inventory.import_yaml(open(__import__("tests.conftest", fromlist=["EXAMPLE"]).EXAMPLE).read())
    db.session.commit()
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0016_scan_macs")
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE trust_level SET name = 'Trusted-Compliant', colour = 'green' WHERE code = 'TC'"))
        conn.execute(text("INSERT INTO trust_level (code, name, rank, description, colour) VALUES ('XX', 'Unused', 1, '', '')"))
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    db.session.remove()
    zones = {z.code: z for z in db.session.scalars(db.select(Zone))}
    assert zones["DC_TC"].name == "Datacenter, Trusted-Compliant" and zones["DC_TC"].rank == 4 and zones["DC_TC"].colour == "#4ade80"
    assert "DC" in zones and zones["DC"].rank is None  # every area has its default
    assert not any(c.endswith("_XX") for c in zones)  # only combinations in use
    assert "trust_level" not in db.inspect(db.engine).get_table_names()


def test_legacy_yaml_and_round_trip(app):
    inventory.import_dict({"areas": [{"code": "DC", "name": "Datacenter"}, {"code": "CA", "name": "Campus"}],
                           "trust_levels": [{"code": "TC", "name": "Trusted", "rank": 4, "colour": "green"}, {"code": "UT", "rank": 1}],
                           "networks": [{"name": "srv", "zone": "DC_TC", "prefix4": "10.0.0.0/24"}]})
    assert naming.all_zones() == ["DC", "DC_TC", "CA"]
    assert naming.zone_long("DC_TC") == "Datacenter, Trusted"
    data = yaml.safe_load(inventory.export_yaml())
    assert "trust_levels" not in data and data["zones"] == [{"code": "DC_TC", "name": "Datacenter, Trusted", "rank": 4, "colour": "#4ade80"}]
    inventory.import_dict(data)
    assert yaml.safe_load(inventory.export_yaml()) == data


def test_default_zones_in_use(admin):
    # a network and host in an area's default zone: no trust level, no placement check
    r = admin.post("/api/networks", json={"name": "lab", "zone": "DC", "prefix4": "10.99.0.0/24"})
    assert r.status_code == 201, r.get_json()
    put_host(admin, "srv-vm-dns-01", zone="DC", zone_pinned=True,
             interfaces=[*host(admin, "srv-vm-dns-01")["interfaces"], {"network": "lab", "ip4": "10.99.0.5"}])
    assert not [i for i in lint.run() if i["code"] == "placement" and i["ref"] == "srv-vm-dns-01"]
    names = {a["name"]: a for a in admin.get("/api/aliases").get_json()}
    assert "NG_ZONE_DC_DEFAULT" in names and "N_ZONE_DC_LAB" in names["NG_ZONE_DC_DEFAULT"]["content"]
    assert "NG_ZONE_DC_DEFAULT" in names["NG_ZONE_DC"]["content"]
    meta = admin.get("/api/meta").get_json()
    dc = next(z for z in meta["zones"] if z["code"] == "DC")
    assert dc["default"] and dc["long"] == "Datacenter" and dc["hex"].startswith("#")
    # colours
    r = admin.put("/api/zones/areas/DC", json={"code": "DC", "name": "Datacenter", "colour": "#ff8800"})
    assert r.status_code == 200
    assert next(z for z in r.get_json()["zones"] if z["code"] == "DC")["hex"] == "#ff8800"
    assert admin.put("/api/zones/areas/DC", json={"code": "DC", "name": "Datacenter", "colour": "orange"}).status_code == 422
