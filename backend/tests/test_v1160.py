"""1.16: features along a dotted path, interface connectors and speeds, cables inside one host,
a host's own colour, VIPs and guests on the topology, changing a cable."""
from .test_v03 import host, put_host


def test_feature_parents_are_made(admin):
    r = admin.post("/api/features", json={"path": "lab.web.nginx", "description": "the leaf"})
    assert r.status_code == 201, r.get_json()
    paths = [f["path"] for f in admin.get("/api/features").get_json()]
    assert {"lab", "lab.web", "lab.web.nginx"} <= set(paths)
    hist = admin.get("/api/history?object_type=feature").get_json()
    assert any(e["object_name"] == "lab.web" and "made for lab.web.nginx" in e["summary"] for e in hist)
    # an existing parent stays as it is
    admin.put("/api/features/lab", json={"path": "lab", "description": "mine"})
    assert admin.post("/api/features", json={"path": "lab.db"}).status_code == 201
    assert admin.get("/api/features/lab").get_json()["description"] == "mine"


def test_interface_connector_and_speed(admin):
    h = host(admin, "net-sw-core-01")
    ifs = [*h["interfaces"], {"label": "sfp1", "kind": "ethernet", "connector": "SFP+", "speed": "10g"},
           {"label": "vlan20", "kind": "vlan", "parent": "sfp1"}]
    d = put_host(admin, "net-sw-core-01", interfaces=ifs)
    sfp = next(i for i in d["interfaces"] if i["label"] == "sfp1")
    assert (sfp["connector"], sfp["speed"]) == ("sfp+", "10G")
    for bad in ({"connector": "hdmi"}, {"speed": "fast"}):
        r = admin.put("/api/hosts/net-sw-core-01", json={**d, "interfaces": [{**sfp, **bad}]})
        assert r.status_code == 422, bad


def test_cable_inside_one_host(admin):
    r = admin.post("/api/topology/cables", json={"a": {"host": "net-sw-core-01", "port": "ge-0/0/46"},
                                                "b": {"host": "net-sw-core-01", "port": "ge-0/0/47"}, "medium": "copper"})
    assert r.status_code == 201, r.get_json()
    sw = host(admin, "net-sw-core-01")
    c = next(c for c in sw["cables"] if c["port"] == "ge-0/0/46")
    assert (c["peer"], c["peer_port"]) == ("net-sw-core-01", "ge-0/0/47")
    labels = [i["label"] for i in sw["interfaces"]]
    assert "ge-0/0/46" in labels and "ge-0/0/47" in labels
    # saving the host as it is keeps it; the same port twice is refused
    put_host(admin, "net-sw-core-01")
    assert len([c for c in host(admin, "net-sw-core-01")["cables"] if c["peer"] == "net-sw-core-01"]) == 1
    r = admin.put("/api/hosts/net-sw-core-01", json={**sw, "cables": [*sw["cables"], {"port": "ge-0/0/45", "peer": "net-sw-core-01", "peer_port": "ge-0/0/47"}]})
    assert r.status_code == 422 and "already has a cable" in str(r.get_json())
    edge = next(e for e in admin.get("/api/topology").get_json()["edges"] if e.get("a_port") == "ge-0/0/46")
    assert edge["source"] == edge["target"] == "h:net-sw-core-01"


def test_change_a_cable(admin):
    admin.post("/api/topology/cables", json={"a": {"host": "srv-vm-dns-01", "port": "eth5"}, "b": {"host": "net-sw-core-01", "port": "ge-0/0/20"}})
    edge = next(e for e in admin.get("/api/topology").get_json()["edges"] if e.get("a_port") == "eth5" or e.get("b_port") == "eth5")
    r = admin.put(f"/api/topology/cables/{edge['link']}", json={"medium": "fiber", "speed": "10G", "notes": "blue, 3 m"})
    assert r.status_code == 200, r.get_json()
    c = next(c for c in host(admin, "srv-vm-dns-01")["cables"] if c["port"] == "eth5")
    assert (c["medium"], c["speed"], c["notes"]) == ("fiber", "10G", "blue, 3 m")
    assert admin.put(f"/api/topology/cables/{edge['link']}", json={"medium": "laser"}).status_code in (404, 422)


def test_host_colour(admin):
    put_host(admin, "srv-vm-dns-01", colour="#FF8800")
    assert host(admin, "srv-vm-dns-01")["colour"] == "#ff8800"
    node = next(n for n in admin.get("/api/topology").get_json()["nodes"] if n.get("name") == "srv-vm-dns-01")
    assert node["colour"] == "#ff8800" and node["fun"] == "unown"
    assert admin.put("/api/hosts/srv-vm-dns-01", json={**host(admin, "srv-vm-dns-01"), "colour": "orange"}).status_code == 422


def test_vips_and_guests_on_the_map(admin):
    # a VIP held by two hosts: joined to them in L3, gone from the physical view
    r = admin.post("/api/hosts", json={"name": "srv-vip-dns-01", "zone": "DC_TC", "kind": "vip", "vip_protocol": "keepalived",
                                       "vip_members": ["srv-vm-dns-01", "srv-vm-mon-01"],
                                       "interfaces": [{"network": "srv", "ip4": "10.10.100.250"}]})
    assert r.status_code == 201, r.get_json()
    l3 = admin.get("/api/topology?view=l3").get_json()
    vip_edges = [e for e in l3["edges"] if e["kind"] == "vip"]
    assert {e["source"] for e in vip_edges} == {"h:srv-vm-dns-01", "h:srv-vm-mon-01"} and vip_edges[0]["target"] == "h:srv-vip-dns-01"
    phys = admin.get("/api/topology").get_json()
    assert not any(n.get("name") == "srv-vip-dns-01" for n in phys["nodes"])
    # a guest without a parent on its interface is joined to the hypervisor's bridge in its network
    pve = host(admin, "srv-pm-pve-02")
    put_host(admin, "srv-pm-pve-02", interfaces=[*pve["interfaces"], {"label": "vmbr1", "kind": "bridge", "network": "srv", "ip4": "10.10.100.2"}])
    phys = admin.get("/api/topology").get_json()
    e = next(e for e in phys["edges"] if e["source"] == "h:srv-vm-dns-01" and e["kind"] == "internal")
    assert e["target"] == "p:srv-pm-pve-02:vmbr1" and e["auto"] is True


def test_new_fields_survive_export_and_import(admin):
    from labdex import inventory
    h = host(admin, "net-sw-core-01")
    put_host(admin, "net-sw-core-01", colour="#22d3ee",
             interfaces=[*h["interfaces"], {"label": "xe-0/1/0", "kind": "ethernet", "connector": "sfp28", "speed": "25G"}])
    data = inventory.export_dict()
    sw = next(x for x in data["hosts"] if x["name"] == "net-sw-core-01")
    assert sw["colour"] == "#22d3ee" and any(i.get("connector") == "sfp28" for i in sw["interfaces"])
    inventory.import_dict(data)
    after = host(admin, "net-sw-core-01")
    assert after["colour"] == "#22d3ee"
    assert any(i["label"] == "xe-0/1/0" and i["connector"] == "sfp28" and i["speed"] == "25G" for i in after["interfaces"])
