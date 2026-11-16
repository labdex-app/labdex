"""Sites: home and public parts of one inventory, each with its own hosts, networks and integrations."""
import pytest
import yaml

from labdex import inventory, sites
from labdex.models import Host, Integration, Network, db

PUBLIC_NET = {"name": "contabo", "site": "public", "zone": "DMZ_TC", "prefix4": "203.0.113.0/24", "gateway4": "203.0.113.1"}


def oak(n=1, **extra):
    return {"name": f"srv-vm-oak-{n:02d}", "site": "public", "zone": "DMZ_TC", "function": "k3s node",
            "interfaces": [{"network": "contabo", "ip4": f"203.0.113.{10 + n}", "primary": True}], **extra}


def add_public(admin):
    r = admin.post("/api/sites", json={"code": "public", "name": "Public", "description": "Contabo and Cloudflare"})
    assert r.status_code == 201, r.get_json()
    assert admin.post("/api/networks", json=PUBLIC_NET).status_code == 201
    r = admin.post("/api/hosts", json=oak(1))
    assert r.status_code == 201, r.get_json()


def test_existing_inventory_is_home(admin):
    s = admin.get("/api/sites").get_json()
    assert [x["code"] for x in s["sites"]] == ["home"] and s["default"] == "home"
    assert s["sites"][0]["usage"]["hosts"] > 0
    assert {h["site"] for h in admin.get("/api/hosts").get_json()} == {"home"}
    assert admin.get("/api/meta").get_json()["sites"] == [{"code": "home", "name": "Home", "description": "", "colour": ""}]
    # a single-site file stays as it was: no site keys anywhere
    text = inventory.export_yaml()
    assert "site:" not in text and "sites:" not in text


def test_create_validate_delete(admin):
    assert admin.post("/api/sites", json={"code": "Bad Code"}).status_code == 422
    assert admin.post("/api/sites", json={"code": "home"}).status_code == 422
    add_public(admin)
    assert admin.put("/api/sites/public", json={"name": "Cloud", "colour": "#7c3aed"}).status_code == 200
    assert admin.put("/api/sites/public", json={"code": "other"}).status_code == 422       # codes don't change
    assert admin.delete("/api/sites/public").status_code == 409                          # still has hosts
    assert admin.delete("/api/hosts/srv-vm-oak-01").status_code in (200, 204)
    assert admin.delete("/api/networks/contabo").status_code in (200, 204)
    assert admin.delete("/api/sites/public").status_code == 200
    r = admin.delete("/api/sites/home")
    assert r.status_code == 409                                                           # hosts, and the last site


def test_hosts_and_networks_stay_in_their_site(admin):
    add_public(admin)
    # a public host can't sit on a home network, nor run on a home hypervisor
    bad = oak(2)
    bad["interfaces"][0]["network"] = "srv"
    bad["interfaces"][0]["ip4"] = "10.10.100.99"
    r = admin.post("/api/hosts", json=bad)
    assert r.status_code == 422 and "belongs to site home" in str(r.get_json())
    home_hv = next(h["name"] for h in admin.get("/api/hosts").get_json() if h["site"] == "home")
    r = admin.post("/api/hosts", json=oak(3, runs_on=home_hv))
    assert r.status_code == 422 and "runs_on" in str(r.get_json())
    assert admin.post("/api/hosts", json={**oak(4), "site": "nowhere"}).status_code == 422
    # filters
    assert [h["name"] for h in admin.get("/api/hosts?site=public").get_json()] == ["srv-vm-oak-01"]
    assert [n["name"] for n in admin.get("/api/networks?site=public").get_json()] == ["contabo"]
    # a network with hosts of its site can't move to another site
    net = admin.get("/api/networks/contabo").get_json()
    r = admin.put("/api/networks/contabo", json={**net, "site": "home"})
    assert r.status_code == 422 and "site" in str(r.get_json())


def test_generators_by_site(admin):
    add_public(admin)
    dns_all = {r["name"] for r in admin.get("/api/dns").get_json()["generated"]}
    dns_public = {r["name"] for r in admin.get("/api/dns?site=public").get_json()["generated"]}
    assert any("srv-vm-oak-01" in n for n in dns_public) and len(dns_public) < len(dns_all)
    assert not any("srv-vm-dns-01" in n for n in dns_public)
    aliases = {a["name"] for a in admin.get("/api/aliases?site=home").get_json()}
    assert not any("OAK" in a for a in aliases)
    with sites.scoped("public"):
        from labdex.generators import hostfiles
        inv = hostfiles.ansible_inventory()
    assert set(inv["all"]["hosts"]) == {"srv-vm-oak-01"}
    assert inv["all"]["hosts"]["srv-vm-oak-01"]["labdex_site"] == "public"
    assert "site_public" in inv["all"]["children"]
    # the scope ends with the block
    assert sites.current() is None


def test_cross_site_alias_members_are_addresses(admin):
    add_public(admin)
    r = admin.post("/api/aliases", json={"name": "Public nodes", "type": "host", "members": ["host:srv-vm-oak-01", "network:contabo"]})
    assert r.status_code == 201, r.get_json()
    home = {a["name"]: a for a in admin.get("/api/aliases?site=home").get_json()}
    assert home["Public nodes"]["content"] == ["203.0.113.11", "203.0.113.0/24"] and not home["Public nodes"]["errors"]
    public = {a["name"]: a for a in admin.get("/api/aliases?site=public").get_json()}
    assert public["Public nodes"]["content"] != home["Public nodes"]["content"]          # in its own site: by alias


def test_integration_sees_its_site(admin):
    add_public(admin)
    r = admin.post("/api/integrations", json={"plugin_id": "exports", "name": "public-exports", "site": "public"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["site"] == "public"
    assert admin.post("/api/integrations", json={"plugin_id": "exports", "name": "x", "site": "mars"}).status_code == 422
    r = admin.post("/api/integrations", json={"plugin_id": "exports", "name": "home-exports"})
    assert r.get_json()["site"] == "home"                                                  # the first site by default
    from labdex.plugins.base import Context
    reg = admin.application.extensions["labdex"]["registry"]
    integ = db.session.scalar(db.select(Integration).where(Integration.name == "public-exports"))
    ctx = Context(integ.name, reg.get("exports"), {}, lambda m: None, integration=integ)
    assert ctx.site == "public"
    assert [h["name"] for h in ctx.inventory.hosts()] == ["srv-vm-oak-01"]
    assert [h["name"] for h in ctx.hosts()] == ["srv-vm-oak-01"]
    assert [n["name"] for n in ctx.inventory.networks()] == ["contabo"]
    assert yaml.safe_load(ctx.inventory.yaml())["site"] == "public"
    assert {s["code"] for s in ctx.inventory.sites()} == {"home", "public"}
    backup = db.session.scalar(db.select(Integration).where(Integration.plugin_id == "backup"))
    assert backup is None or backup.site == ""                                             # backups cover every site


def test_site_scopes_in_roles(admin, make_user):
    add_public(admin)
    pub = make_user("pub", role="viewer", scopes=["site:public"])
    assert [h["name"] for h in pub.get("/api/hosts").get_json()] == ["srv-vm-oak-01"]
    r = admin.put("/api/integrations/backup", json={"selectors": ["site:public"]})
    assert r.status_code in (200, 404)


def test_yaml_round_trip_and_site_files(admin):
    add_public(admin)
    full = yaml.safe_load(inventory.export_yaml())
    assert [s["code"] for s in full["sites"]] == ["home", "public"]
    oak_d = next(h for h in full["hosts"] if h["name"] == "srv-vm-oak-01")
    assert oak_d["site"] == "public"
    assert "site" not in next(h for h in full["hosts"] if h["name"] == "srv-vm-dns-01")  # the first site isn't repeated
    # a full import keeps them apart
    inventory.import_yaml(inventory.export_yaml())
    assert db.session.scalar(db.select(Host).where(Host.name == "srv-vm-oak-01")).site == "public"

    # one site's file: only that site changes
    pub = yaml.safe_load(admin.get("/api/sites/public/export").get_data(as_text=True))
    assert pub["site"] == "public" and [h["name"] for h in pub["hosts"]] == ["srv-vm-oak-01"] and "zones" not in pub
    home_before = db.session.scalar(db.select(db.func.count()).select_from(Host).where(Host.site == "home"))
    pub["hosts"].append(oak(2))
    pub["hosts"][0]["site"] = "home"                                                      # the file decides
    counts = inventory.import_yaml(yaml.safe_dump(pub))
    assert counts == {"site": "public", "networks": 1, "hosts": 2}
    assert db.session.scalar(db.select(db.func.count()).select_from(Host).where(Host.site == "home")) == home_before
    assert {h.site for h in db.session.scalars(db.select(Host).where(Host.name.like("srv-vm-oak-%")))} == {"public"}
    # an error leaves everything as it was
    pub["hosts"].append({"name": "Bad Name", "zone": "DMZ_TC"})
    from labdex.store import ValidationError
    with pytest.raises(ValidationError) as exc:
        inventory.import_yaml(yaml.safe_dump(pub))
    assert "hosts[2]" in str(exc.value.errors)
    assert db.session.scalar(db.select(db.func.count()).select_from(Host).where(Host.site == "public")) == 2
    # a new site comes with its first file
    inventory.import_yaml(yaml.safe_dump({"site": "lab2", "networks": [], "hosts": []}))
    assert sites.exists("lab2")
    assert db.session.scalar(db.select(Network).where(Network.name == "contabo")).site == "public"


def test_cli_site_export_import(loaded, tmp_path):
    runner = loaded.test_cli_runner()
    r = runner.invoke(args=["sites"])
    assert "home" in r.output
    out = tmp_path / "home.yaml"
    r = runner.invoke(args=["export", "--site", "home", "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert yaml.safe_load(out.read_text())["site"] == "home"
    r = runner.invoke(args=["import", "--yes", str(out)])
    assert r.exit_code == 0 and "Site home" in r.output, r.output
    assert runner.invoke(args=["export", "--site", "mars"]).exit_code != 0
