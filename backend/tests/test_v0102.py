"""0.10.2: tags known everywhere, naming lists, hosts tabs."""
import yaml

from labdex import inventory

from .test_v03 import host, put_host


def test_tags(admin, make_user):
    tags = {t["name"]: t for t in admin.get("/api/tags").get_json()}
    assert "admin" in tags and tags["admin"]["hosts"] >= 1  # made from the hosts' tags
    put_host(admin, "srv-vm-dns-01", tags=["dns", "backup"])
    assert "dns" in {t["name"] for t in admin.get("/api/tags").get_json()}
    h = host(admin, "srv-vm-dns-01")
    assert admin.put("/api/hosts/srv-vm-dns-01", json={**h, "tags": ["no spaces please"]}).status_code == 422
    # rename follows hosts and role scopes; colour and description
    r = admin.put("/api/tags/dns", json={"name": "resolver", "colour": "#22d3ee", "description": "answers DNS"})
    assert r.status_code == 200, r.get_json()
    assert "resolver" in host(admin, "srv-vm-dns-01")["tags"]
    assert admin.put("/api/tags/resolver", json={"name": "resolver", "colour": "cyan"}).status_code == 422
    # editors may add tags but not rename them
    ed = make_user("ed", role="editor")
    assert ed.post("/api/tags", json={"name": "lab"}).status_code == 201
    assert ed.put("/api/tags/lab", json={"name": "labs"}).status_code == 403
    # YAML keeps colours and descriptions
    data = yaml.safe_load(inventory.export_yaml())
    assert {"name": "resolver", "colour": "#22d3ee", "description": "answers DNS"} in data["tags"]
    inventory.import_dict(data)
    assert next(t for t in admin.get("/api/tags").get_json() if t["name"] == "resolver")["colour"] == "#22d3ee"
    # delete removes it from hosts
    assert admin.delete("/api/tags/resolver").status_code == 204
    assert "resolver" not in host(admin, "srv-vm-dns-01")["tags"]


def test_pools_list_used_names(admin):
    put_host(admin, "srv-vm-dns-01", fun_names=["pikachu"])
    pools = admin.get("/api/naming/pools").get_json()
    assert any("pikachu" in p["used"] for p in pools if "pikachu" in p["names"]) or not any("pikachu" in p["names"] for p in pools)
