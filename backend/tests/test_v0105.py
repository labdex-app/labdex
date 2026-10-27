"""0.10.5: alias uids, importing the firewall's aliases, allow-from sources, host-wide rules."""
import re

import pytest

from labdex import naming, store
from labdex.models import AliasGroup, Setting, db

from .fakes import FakeOPNsense
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


@pytest.fixture
def opn():
    o = FakeOPNsense()
    yield o
    o.srv.close()


def test_uids_by_default(admin):
    store.save_settings({"alias_ids": "uid"})
    db.session.commit()
    aliases = admin.get("/api/aliases").get_json()
    gen = [a for a in aliases if a["kind"] != "custom" or not a.get("fw_id_set")]
    assert all(re.fullmatch(r"ldx_[0-9a-f]{8}", a["fw_id"]) for a in gen if a["kind"] != "custom")
    assert naming.firewall_id("N_ZONE_DC_TC_SRV") == naming.firewall_id("N_ZONE_DC_TC_SRV")  # stable
    assert admin.put("/api/admin/settings", json={"alias_ids": "fancy"}).status_code == 422


def test_existing_instances_keep_readable_ids(app, loaded):
    from alembic import command
    from labdex import schema
    db.session.execute(db.delete(Setting).where(Setting.key == "alias_ids"))
    db.session.commit()
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0019_interfaces")
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    assert db.session.get(Setting, "alias_ids").value == "readable"


def test_import_firewall_aliases(admin, opn):
    opn.add("HG_OFFICE", "10.10.10.200\n10.10.10.201", "office PCs")
    opn.add("NG_ALL", "HG_OFFICE\n10.0.0.0/8", "nested", type_="network")
    opn.add("PG_WEB", "80\n443", "", type_="port")
    opn.add("bogons", "", "", type_="external")
    opn.add("GEO_CH", "CH", "", type_="geoip")
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": opn.KEY, "api_secret": opn.SECRET})
    job = run(admin, "fw", "import_aliases")
    assert job["status"] == "succeeded" and job["result"]["imported"] == 3, admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    g = {a.name: a for a in db.session.scalars(db.select(AliasGroup))}
    assert sorted(g["NG_ALL"].members) == ["10.0.0.0/8", "alias:HG_OFFICE"] and g["NG_ALL"].fw_id == "NG_ALL" and g["PG_WEB"].type == "port"
    assert "GEO_CH" not in g and "bogons" not in g
    assert run(admin, "fw", "import_aliases")["result"]["imported"] == 0  # already there
    # labdex now plans no rename for them
    plan = run(admin, "fw", "sync_aliases")
    ch = admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"] if plan["status"] == "planned" else []
    assert not any(c["name"] in ("HG_OFFICE", "NG_ALL") and c["op"] == "create" for c in ch)


def test_sources_and_host_rules(admin, opn):
    src = admin.get("/api/services/sources").get_json()
    assert {"cidr": "10.10.100.0/24", "network": "srv"} in src["prefixes"]
    h = host(admin, "srv-vm-dns-01")
    r = admin.put("/api/hosts/srv-vm-dns-01", json={**h, "rules": [{"direction": "sideways", "peer": "", "ports": ["tcp/99999"]}]})
    fields = {e["field"] for e in r.get_json()["errors"]}
    assert {"rules[0].direction", "rules[0].peer", "rules[0].ports"} <= fields
    put_host(admin, "srv-vm-dns-01", rules=[{"direction": "in", "peer": "mgmt", "ports": ["any"], "description": "admins"},
                                            {"direction": "out", "peer": "any", "ports": ["tcp/443"]}])
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": opn.KEY, "api_secret": opn.SECRET})
    plan = run(admin, "fw", "sync_rules")
    rows = {c["data"]["key"]: c["after"] for c in admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]}
    inn, out = rows["host:mgmt>srv-vm-dns-01:any"], rows["host:srv-vm-dns-01>any:tcp/443"]
    assert inn["source_net"].startswith("N") and inn["destination_net"].startswith("H") and inn["description"].endswith("[host:mgmt>srv-vm-dns-01:any]")
    assert out["source_net"].startswith("H") and out["destination_net"] == "any" and out["destination_port"] == "443" and out["protocol"] == "TCP"
