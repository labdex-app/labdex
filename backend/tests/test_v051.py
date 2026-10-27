"""v0.5.1: home zones that follow the primary interface, zones per address, VIP hosts."""
import yaml

from labdex import inventory
from labdex.generators import hostfiles
from labdex.models import Host, db

from .test_v03 import host, put_host

VIP = {"name": "net-vip-k3s-01", "kind": "vip", "vip_protocol": "kube-vip", "function": "k3s API",
       "vip_members": ["srv-vm-k3s-01", "srv-vm-k3s-02"],
       "interfaces": [{"network": "k8s", "ip4": "10.10.128.10", "primary": True}],
       "services": [{"name": "k3s-api", "port": 6443, "scheme": "https", "dns_name": "k3s-api"}]}


# ------------------------------------------------------------------- zones
def test_zone_follows_primary_interface(admin):
    r = admin.post("/api/hosts", json={"name": "srv-vm-new-01", "interfaces": [{"network": "srv", "ip4": "10.10.100.77", "primary": True}]})
    assert r.status_code == 201, r.get_json()
    h = r.get_json()
    assert h["zone"] == "DC_TC" and h["zone_pinned"] is False
    assert h["interfaces"][0]["zone"] == "DC_TC"
    # primary moves to mgmt: the home zone moves along
    h = put_host(admin, "srv-vm-new-01", interfaces=[{"network": "srv", "ip4": "10.10.100.77"},
                                                     {"network": "mgmt", "ip4": "10.10.3.77", "primary": True}])
    assert h["zone"] == "DC_HT" and [i["zone"] for i in h["interfaces"]] == ["DC_TC", "DC_HT"]
    # pinned: stays where it was put
    h = put_host(admin, "srv-vm-new-01", zone="DC_TC", zone_pinned=True)
    assert h["zone"] == "DC_TC" and h["zone_pinned"] is True
    # no zone and no network: refused with a clear message
    r = admin.post("/api/hosts", json={"name": "srv-vm-nozone-01"})
    assert r.status_code == 422 and "interface in a network" in r.get_json()["errors"][0]["message"]
    # older clients and YAML without zone_pinned: a zone different from the network's means pinned
    r = admin.post("/api/hosts", json={"name": "srv-vm-old-01", "zone": "DC_HT",
                                       "interfaces": [{"network": "srv", "ip4": "10.10.100.78"}]})
    assert r.get_json()["zone"] == "DC_HT" and r.get_json()["zone_pinned"] is True


def test_network_zone_change_moves_following_hosts(admin):
    put_host(admin, "srv-vm-mon-01", zone="DC_TC", zone_pinned=True)  # pinned, stays
    net = admin.get("/api/networks/srv").get_json()
    r = admin.put("/api/networks/srv", json={**net, "zone": "DC_HT"})
    assert r.status_code == 200, r.get_json()
    assert host(admin, "srv-vm-dns-01")["zone"] == "DC_HT"
    assert host(admin, "srv-vm-mon-01")["zone"] == "DC_TC"
    ev = admin.get("/api/history?object_name=srv-vm-dns-01").get_json()[0]
    assert "with network srv" in ev["summary"]


def test_view_through_address_zone_only(make_user):
    v = make_user("dmzview", role="editor", scopes=["zone:DMZ_*"])
    fw = v.get("/api/hosts/net-fw-edge-01")
    assert fw.status_code == 200 and fw.get_json()["can_edit"] is False
    tree = v.get("/api/nav").get_json()["tree"]
    dmz = next(z for z in tree if z["zone"] == "DMZ_TC")
    assert any(h["name"] == "net-fw-edge-01" for n in dmz["networks"] for h in n["hosts"])
    assert all(z["zone"].startswith("DMZ") for z in tree)


# --------------------------------------------------------------------- VIPs
def test_vip_host(admin):
    r = admin.post("/api/hosts", json=VIP)
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    assert v["kind"] == "vip" and v["vip_members"] == ["srv-vm-k3s-01", "srv-vm-k3s-02"] and v["zone"] == "DC_TC"
    assert any(d["fqdn"].startswith("net-vip-k3s-01.") for d in v["dns"])
    assert any(a["name"].startswith("H_ZONE_DC_TC_NET_VIP_K3S_01") for a in v["aliases"])
    m = host(admin, "srv-vm-k3s-01")
    assert m["vips"] == [{"name": "net-vip-k3s-01", "fun": "", "protocol": "kube-vip",
                          "addresses": [{"network": "k8s", "ip4": "10.10.128.10", "ip6": ""}]}]
    # the address is used once: no duplicate-ip lint, IPAM shows the VIP
    assert not [i for i in admin.get("/api/lint").get_json()["issues"] if i["code"] == "duplicate-ip"]
    # Ansible: not a target; members get labdex_vips and a group
    inv = hostfiles.ansible_inventory()["all"]
    assert "net-vip-k3s-01" not in inv["hosts"]
    assert set(inv["children"]["vip_net_vip_k3s_01"]["hosts"]) == {"srv-vm-k3s-01", "srv-vm-k3s-02"}
    lv = inv["hosts"]["srv-vm-k3s-01"]["labdex_vips"][0]
    assert lv["peers"] == ["srv-vm-k3s-02"] and lv["addresses"] == [{"network": "k8s", "ip4": "10.10.128.10"}]
    # a member's rename follows; a deleted member drops out
    put_host(admin, "srv-vm-k3s-02", name="srv-vm-k3s-03")
    assert host(admin, "net-vip-k3s-01")["vip_members"] == ["srv-vm-k3s-01", "srv-vm-k3s-03"]
    admin.delete("/api/hosts/srv-vm-k3s-03")
    assert host(admin, "net-vip-k3s-01")["vip_members"] == ["srv-vm-k3s-01"]
    assert admin.get("/api/hosts?fields=name,kind").get_json()[0].keys() == {"name", "kind"}


def test_vip_validation(admin):
    def err(data):
        r = admin.post("/api/hosts", json=data)
        assert r.status_code == 422, r.get_json()
        return " ".join(e["message"] for e in r.get_json()["errors"])
    assert "needs its address" in err({**VIP, "interfaces": []})
    assert "no MAC" in err({**VIP, "interfaces": [{"network": "k8s", "ip4": "10.10.128.10", "mac": "02:00:00:00:00:01"}]})
    assert "unknown host" in err({**VIP, "vip_members": ["nope"]})
    assert "only a VIP" in err({"name": "srv-vm-x-01", "zone": "DC_TC", "vip_members": ["srv-vm-k3s-01"]})
    assert admin.post("/api/hosts", json=VIP).status_code == 201
    assert "is a VIP itself" in err({**VIP, "name": "net-vip-k3s-02", "vip_members": ["net-vip-k3s-01"],
                                     "interfaces": [{"network": "k8s", "ip4": "10.10.128.9"}]})
    # a member can't become a VIP while it's a member
    r = admin.put("/api/hosts/srv-vm-k3s-01", json={**host(admin, "srv-vm-k3s-01"), "kind": "vip"})
    assert r.status_code == 422 and "remove it there first" in str(r.get_json())


def test_vip_yaml_roundtrip(admin):
    assert admin.post("/api/hosts", json=VIP).status_code == 201
    text = inventory.export_yaml()
    data = yaml.safe_load(text)
    vip = next(h for h in data["hosts"] if h["name"] == "net-vip-k3s-01")
    assert vip["kind"] == "vip" and vip["vip_members"] == ["srv-vm-k3s-01", "srv-vm-k3s-02"]
    assert all("vips" not in h and "kind" not in h for h in data["hosts"] if h["name"] != "net-vip-k3s-01")
    assert all("zone" not in i for h in data["hosts"] for i in h.get("interfaces", []))
    inventory.import_yaml(text)  # members point forward (net-… sorts before srv-…)
    v = db.session.scalar(db.select(Host).where(Host.name == "net-vip-k3s-01"))
    assert [m.name for m in v.vip_members] == ["srv-vm-k3s-01", "srv-vm-k3s-02"]
    assert yaml.safe_load(inventory.export_yaml()) == data


# --------------------------------------------------------------- migration
def test_migration_pins_mismatched_zones(app):
    from alembic import command
    from sqlalchemy import text
    from labdex import schema
    inventory.import_yaml(open(__import__("tests.conftest", fromlist=["EXAMPLE"]).EXAMPLE).read())
    db.session.commit()
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0001_baseline")
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE host SET zone = 'CA_ST' WHERE name = 'srv-vm-dns-01'"))
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    pinned = {h.name: h.zone_pinned for h in db.session.scalars(db.select(Host))}
    assert pinned["srv-vm-dns-01"] is True and pinned["srv-vm-mon-01"] is False
    assert len(db.session.scalar(db.select(Host).where(Host.name == "srv-vm-mon-01")).interfaces) == 1  # nothing cascaded away
