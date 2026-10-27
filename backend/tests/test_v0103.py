"""0.10.3: named, typed interfaces with secondary addresses, a logical stack, cables, vendor."""
import yaml

from labdex import discovery, inventory, lint
from labdex.models import db

from .test_v03 import host, put_host


def ifaces(admin, name):
    return host(admin, name)["interfaces"]


def test_interfaces_stack_and_extra_addresses(admin):
    i = ifaces(admin, "srv-pm-pve-01")
    i[0].update(label="vmbr0", kind="bridge", parent="bond0", extra_ips=["10.10.3.111", "10.10.3.111", "fd00:10:10:3::111"])
    i += [{"label": "bond0", "kind": "bond"}, {"label": "eno1", "kind": "ethernet", "parent": "bond0"}]
    h = put_host(admin, "srv-pm-pve-01", interfaces=i)
    got = {x["label"]: x for x in h["interfaces"]}
    assert got["vmbr0"]["extra_ips"] == ["10.10.3.111", "fd00:10:10:3::111"] and got["eno1"]["parent"] == "bond0"
    # secondary addresses: in the host's aliases and taken in IPAM, not in DNS
    names = {a["name"]: a for a in admin.get("/api/aliases").get_json()}
    h4 = next(a for n, a in names.items() if n.startswith("H4_") and "SRV_PM_PVE_01" in n)
    assert "10.10.3.111" in h4["content"]
    assert not any(r["value"] == "10.10.3.111" for r in h["dns"])
    used = admin.get("/api/networks/mgmt").get_json()
    assert "10.10.3.111" in str(used)
    # errors
    bad = [*i, {"label": "x", "kind": "warp"}, {"label": "y", "parent": "nope"}]
    r = admin.put("/api/hosts/srv-pm-pve-01", json={**h, "interfaces": bad})
    fields = {e["field"] for e in r.get_json()["errors"]}
    assert r.status_code == 422 and f"interfaces[{len(i)}].kind" in fields and f"interfaces[{len(i) + 1}].parent" in fields
    # a VM NIC on its hypervisor's bridge; lint notices when the bridge goes away
    vm = ifaces(admin, "srv-vm-dns-01")
    vm[0]["parent"] = "srv-pm-pve-01:vmbr0"
    put_host(admin, "srv-vm-dns-01", interfaces=vm)
    assert not [x for x in lint.run() if x["code"] == "interface-parent"]
    vm[0]["parent"] = "srv-pm-pve-01:vmbr9"
    put_host(admin, "srv-vm-dns-01", interfaces=vm)
    assert [x for x in lint.run() if x["code"] == "interface-parent" and x["ref"] == "srv-vm-dns-01"]


def test_cables(admin):
    h = put_host(admin, "srv-pm-pve-01", cables=[{"port": "eno1", "peer": "net-sw-core-01", "peer_port": "ge-0/0/1", "medium": "copper", "speed": "1G"}])
    assert h["cables"][0]["peer"] == "net-sw-core-01"
    sw = host(admin, "net-sw-core-01")
    assert sw["cables"] == [{"port": "ge-0/0/1", "peer": "srv-pm-pve-01", "peer_port": "eno1", "medium": "copper", "speed": "1G", "notes": "", "label": "", "length": "", "colour": ""}]
    # re-plugging the switch port elsewhere moves the cable
    put_host(admin, "srv-pm-pve-02", cables=[{"port": "eno1", "peer": "net-sw-core-01", "peer_port": "ge-0/0/1"}])
    assert host(admin, "srv-pm-pve-01")["cables"] == []
    r = admin.put("/api/hosts/srv-pm-pve-02", json={**host(admin, "srv-pm-pve-02"), "cables": [
        {"port": "eno1", "peer": "nobody", "peer_port": "x"}, {"port": "eno1", "peer": "net-sw-core-01", "peer_port": "ge-0/0/2", "medium": "string"}]})
    fields = {e["field"] for e in r.get_json()["errors"]}
    assert {"cables[0].peer", "cables[1].medium", "cables[1].port"} <= fields
    # YAML: once per cable, both ends after import
    data = yaml.safe_load(inventory.export_yaml())
    assert sum(len(x.get("cables", [])) for x in data["hosts"]) == 1
    inventory.import_dict(data)
    assert host(admin, "net-sw-core-01")["cables"][0]["peer"] == "srv-pm-pve-02"
    # deleting a host takes its cables along
    assert admin.delete("/api/hosts/srv-pm-pve-02").status_code in (204, 409)


def test_vendor_and_seen(admin, app):
    with app.app_context():
        discovery.record_neighbours("opnsense:fw", [{"ip": "10.10.100.77", "mac": "aa:bb:cc:00:00:77", "hostname": "nas", "vendor": "Synology"},
                                                    {"ip": "10.10.100.53", "mac": "52:54:00:00:00:53", "hostname": "dns"}])
        db.session.commit()
    r = admin.post("/api/discovery/addresses/assign", json={"ip": "10.10.100.77", "host": "srv-vm-mon-01"})
    assert r.status_code == 201 and r.get_json()["vendor"] == "Synology"
    seen = host(admin, "srv-vm-dns-01")["seen"]
    assert seen["10.10.100.53"]["hostname"] == "dns" and seen["10.10.100.53"]["mac"] == "52:54:00:00:00:53"
    h = put_host(admin, "srv-vm-dns-01", vendor="QEMU", model="pc-q35", icon="vm")
    assert (h["vendor"], h["model"], h["icon"]) == ("QEMU", "pc-q35", "vm")
