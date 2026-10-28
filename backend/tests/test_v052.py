"""v0.5.2: function field, free-text hostname roles, fun name pools, per-host DNS records, About."""
import json

import yaml

from labdex import funpools, inventory
from labdex.generators import dns, hostfiles
from labdex.models import FunPool, Host, HostRecord, db

from .test_plugins import make_instance
from .test_v03 import host, put_host


# ------------------------------------------------------------------ function
def test_function_field_and_old_role_key(admin):
    r = admin.post("/api/hosts", json={"name": "srv-vm-old-01", "role": "legacy client", "interfaces": [{"network": "srv", "ip4": "10.10.100.90"}]})
    h = r.get_json()
    assert r.status_code == 201 and h["function"] == "legacy client" and "role" not in h
    h = put_host(admin, "srv-vm-old-01", function="Proxy")
    assert h["function"] == "Proxy"
    hv = hostfiles.ansible_inventory()["all"]["hosts"]["srv-vm-old-01"]
    assert hv["labdex_function"] == "Proxy" and hv["labdex_role"] == "Proxy"
    assert "function: Proxy" in inventory.export_yaml()


def test_free_text_hostname_role(admin):
    n = admin.get("/api/naming/next?category=srv&type=vm&role=zigbee").get_json()
    assert n["name"] == "srv-vm-zigbee-01" and n["unknown"] == []
    assert admin.get("/api/naming/next?category=srv&type=vm&role=Zig Bee").status_code == 422
    admin.post("/api/hosts", json={"name": "srv-vm-zigbee-01", "interfaces": [{"network": "srv", "ip4": "10.10.100.91"}]})
    assert "zigbee" in admin.get("/api/naming").get_json()["roles_in_use"]
    assert admin.get("/api/naming/next?category=srv&type=vm&role=zigbee").get_json()["name"] == "srv-vm-zigbee-02"


# --------------------------------------------------------------------- pools
def test_builtin_pools_and_suggestions(admin, app):
    pools = {p["name"]: p for p in admin.get("/api/naming/pools").get_json()}
    assert set(pools) == {"greek-gods", "stars", "trees", "minerals"} and all(p["builtin"] for p in pools.values())
    s = admin.get("/api/naming/fun-suggest?category=srv&zone=DC_TC&count=5").get_json()
    assert len(s["names"]) == 5 and s["matched"] is False  # no pool applies to srv: all pools
    # a pool of your own, for servers
    r = admin.post("/api/naming/pools", json={"name": "pokemon", "names": "arcanine, snorlax\nbulbasaur ivysaur", "applies_to": "category:srv"})
    assert r.status_code == 201, r.get_json()
    s = admin.get("/api/naming/fun-suggest?category=srv&zone=DC_TC&count=10").get_json()
    assert s["matched"] and s["pools"] == ["pokemon"]
    assert {x["name"] for x in s["names"]} == {"bulbasaur", "ivysaur"}  # arcanine and snorlax are taken in the example
    # explicit pool, validation
    assert all(x["pool"] == "stars" for x in admin.get("/api/naming/fun-suggest?pool=stars").get_json()["names"])
    r = admin.post("/api/naming/pools", json={"name": "bad", "names": "Ok, 9lives", "applies_to": "nonsense"})
    errs = {e["field"] for e in r.get_json()["errors"]}
    assert r.status_code == 422 and errs == {"names", "applies_to"}
    # a deleted built-in stays deleted
    assert admin.delete("/api/naming/pools/trees").status_code == 204
    funpools.ensure_builtin()
    assert db.session.scalar(db.select(FunPool).where(FunPool.name == "trees")) is None


def test_pools_in_yaml(admin):
    admin.post("/api/naming/pools", json={"name": "pokemon", "names": "bulbasaur ivysaur", "applies_to": ["category:srv"]})
    data = yaml.safe_load(inventory.export_yaml())
    pools = {p["name"]: p for p in data["fun_pools"]}
    assert pools["stars"] == {"name": "stars", "builtin": True}  # unchanged built-in: no names in the file
    assert pools["pokemon"]["names"] == ["bulbasaur", "ivysaur"]
    assert "fun_pools_seeded" not in data["settings"]
    inventory.import_yaml(yaml.safe_dump(data))
    assert len(db.session.scalar(db.select(FunPool).where(FunPool.name == "stars")).names) > 40
    assert yaml.safe_load(inventory.export_yaml())["fun_pools"] == data["fun_pools"]


# ----------------------------------------------------------------------- DNS
def test_host_records_and_fun_dns(admin):
    h = put_host(admin, "srv-vm-dns-01", fun_names=["unown", "resolver"], fun_no_dns=["resolver"], records=[
        {"name": "ns1", "type": "CNAME", "value": ""},
        {"name": "_acme-challenge.{host}", "type": "TXT", "value": "token-123"},
        {"name": "", "type": "MX", "value": "10 {fqdn}"},
        {"name": "v4", "type": "A", "value": "{ip4}"},
        {"name": "web", "type": "SRV", "value": "0 5 443 nas"},
    ])
    recs = {(r["fqdn"], r["type"]): r["value"] for r in h["dns"]}
    d = "lab.example.net"
    assert recs[(f"ns1.{d}", "CNAME")] == f"srv-vm-dns-01.srv.{d}."           # follows the primary record
    assert recs[(f"_acme-challenge.srv-vm-dns-01.{d}", "TXT")] == '"token-123"'
    assert recs[(f"srv-vm-dns-01.{d}", "MX")] == f"10 srv-vm-dns-01.{d}."
    assert recs[(f"v4.{d}", "A")] == "10.10.100.53"
    assert recs[(f"web.{d}", "SRV")] == f"0 5 443 nas.{d}."
    assert (f"unown.{d}", "CNAME") in recs and (f"resolver.{d}", "CNAME") not in recs
    # the primary moves: the alias follows
    put_host(admin, "srv-vm-dns-01", interfaces=[{"network": "srv", "ip4": "10.10.100.53"}, {"network": "mgmt", "ip4": "10.10.3.53", "primary": True}])
    recs = {(r["fqdn"], r["type"]): r["value"] for r in host(admin, "srv-vm-dns-01")["dns"]}
    assert recs[(f"ns1.{d}", "CNAME")] == f"srv-vm-dns-01.mgmt.{d}."
    # YAML keeps them
    data = yaml.safe_load(inventory.export_yaml())
    yh = next(x for x in data["hosts"] if x["name"] == "srv-vm-dns-01")
    assert yh["fun_no_dns"] == ["resolver"] and yh["records"][0] == {"name": "ns1", "type": "CNAME"}


def test_host_record_validation_and_lint(admin):
    def errors(records, **kw):
        r = admin.put("/api/hosts/srv-vm-dns-01", json={**host(admin, "srv-vm-dns-01"), "records": records, **kw})
        assert r.status_code == 422, r.get_json()
        return {e["field"]: e["message"] for e in r.get_json()["errors"]}
    assert "records[0].value" in errors([{"type": "A", "value": "x"}])
    assert "IPv6" in errors([{"type": "AAAA", "value": "10.0.0.1"}])["records[0].value"]
    assert "e.g. 10 mail" in errors([{"type": "MX", "value": "mail"}])["records[0].value"]
    assert "records[0].type" in errors([{"type": "NS", "value": "x"}])
    assert "outside" in errors([{"name": "www.example.org.", "type": "CNAME"}])["records[0].name"]
    assert "placeholder" in errors([{"type": "TXT", "value": "{nope}"}])["records[0].value"]
    assert "fun_no_dns" in errors([], fun_no_dns=["not-mine"])
    # a name another host already has is a conflict
    put_host(admin, "srv-vm-dns-01", records=[{"name": "srv-vm-mon-01", "type": "A", "value": "{ip4}"}])
    issues = [i for i in admin.get("/api/lint").get_json()["issues"] if i["code"] == "dns-conflict"]
    assert issues and "srv-vm-mon-01" in issues[0]["message"]


def test_migration_moves_technitium_host_records(admin, app):
    from alembic import command
    from sqlalchemy import text
    from labdex import schema
    make_instance(admin, "technitium", "dns", {"url": "http://127.0.0.1:9", "token": "t"})
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0002_vip_zone_follow")
    with db.engine.begin() as conn:
        iid = conn.execute(text("SELECT id FROM integration WHERE name = 'dns'")).scalar()
        conn.execute(text("INSERT INTO host_module (integration_id, host_name, enabled, settings) VALUES (:i, 'srv-vm-dns-01', NULL, :s)"),
                     {"i": iid, "s": json.dumps({"records": [{"name": "resolver", "type": "A", "value": "{ip4}"}], "skip_generated": False})})
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    h = db.session.scalar(db.select(Host).where(Host.name == "srv-vm-dns-01"))
    assert [(r.name, r.type, r.value) for r in h.records] == [("resolver", "A", "{ip4}")]
    assert h.function == "DNS resolver"  # the column was renamed, the data kept
    with db.engine.connect() as conn:
        s = json.loads(conn.execute(text("SELECT settings FROM host_module WHERE host_name = 'srv-vm-dns-01'")).scalar())
    assert s == {"skip_generated": False}
    assert db.session.scalar(db.select(db.func.count()).select_from(HostRecord)) == 1
    assert any(r.name == "resolver" for r in dns.records())


# --------------------------------------------------------------------- about
def test_about(admin, monkeypatch):
    a = admin.get("/api/about").get_json()
    assert a["version"] and a["schema"]["current"] == a["schema"]["newest"]
    assert a["database"]["backend"] in ("sqlite", "postgresql") and "## 0.5.2" in a["changelog"]
    assert any(p["id"] == "technitium" and p["trust"] == "official" for p in a["plugins"])
    assert a["update_check"] is None
    from labdex.api import about_api
    monkeypatch.setattr(about_api, "latest_release", lambda url, force=False: {"tag": "v9.0.0", "name": "", "url": "https://example.org", "published": ""})
    admin.put("/api/admin/settings", json={"update_check": "true", "update_check_repo": "someone/labdex"})
    a = admin.get("/api/about").get_json()
    assert a["update_check"]["newer"] is True and a["project_url"] == "https://github.com/someone/labdex"  # the old setting still counts
