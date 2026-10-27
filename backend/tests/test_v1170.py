"""1.17: cable labels, lengths and colours; the cable list; saved topology views."""
from labdex import inventory

from .test_v03 import host, put_host


def cable(admin, a, ap, b, bp, **kw):
    r = admin.post("/api/topology/cables", json={"a": {"host": a, "port": ap}, "b": {"host": b, "port": bp}, **kw})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_cable_label_length_colour(admin):
    assert admin.get("/api/cables").get_json()["next_label"] == "C-0001"
    cable(admin, "srv-vm-dns-01", "eth5", "net-sw-core-01", "ge-0/0/5", label="C-0007", length="3,5 m", colour="blue")
    c = next(c for c in host(admin, "srv-vm-dns-01")["cables"] if c["port"] == "eth5")
    assert (c["label"], c["length"], c["colour"]) == ("C-0007", "3.5", "blue")
    lst = admin.get("/api/cables").get_json()
    assert lst["next_label"] == "C-0008"
    row = next(x for x in lst["cables"] if x["label"] == "C-0007")
    assert (row["a_host"], row["b_port"], row["length"]) == ("srv-vm-dns-01", "ge-0/0/5", "3.5")
    # a label is one cable's only
    r = admin.post("/api/topology/cables", json={"a": {"host": "srv-vm-mon-01", "port": "eth6"}, "b": {"host": "net-sw-core-01", "port": "ge-0/0/6"},
                                                "label": "C-0007"})
    assert r.status_code == 422 and "on another cable" in str(r.get_json())
    r = admin.put(f"/api/topology/cables/{row['id']}", json={"length": "far"})
    assert r.status_code == 422
    assert admin.put(f"/api/topology/cables/{row['id']}", json={"label": "C-0010", "length": "2"}).status_code == 200
    assert admin.get("/api/cables").get_json()["next_label"] == "C-0011"
    edge = next(e for e in admin.get("/api/topology").get_json()["edges"] if e.get("cable_label") == "C-0010")
    assert edge["length"] == "2"


def test_labels_survive_export_and_import(admin):
    cable(admin, "srv-vm-dns-01", "eth5", "net-sw-core-01", "ge-0/0/5", label="P-12", length="1", colour="yellow")
    inventory.import_dict(inventory.export_dict())
    c = next(c for c in host(admin, "srv-vm-dns-01")["cables"] if c["port"] == "eth5")
    assert (c["label"], c["length"], c["colour"]) == ("P-12", "1", "yellow")
    put_host(admin, "srv-vm-dns-01")  # saving the host as it is keeps its label (it's its own)
    assert next(c for c in host(admin, "srv-vm-dns-01")["cables"] if c["port"] == "eth5")["label"] == "P-12"


def test_cable_list_respects_rights(admin, make_user):
    cable(admin, "srv-vm-dns-01", "eth5", "net-sw-core-01", "ge-0/0/5")
    u = make_user("z", role="viewer", scopes=["zone:DC_TC"])
    assert all(x["a_zone"] == "DC_TC" and x["b_zone"] == "DC_TC" for x in u.get("/api/cables").get_json()["cables"])


def test_saved_views(admin, make_user):
    r = admin.post("/api/topology/views", json={"name": "Kube networks", "config": {
        "view": "l3", "zone": "dc_tc", "hidden_nets": ["oob"], "grid": True, "camera": {"zoom": 1.25, "x": 10, "y": -4}, "junk": 1}})
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    assert v["config"] == {"view": "l3", "zone": "DC_TC", "hidden_nets": ["oob"], "lonely": False, "grid": True, "camera": {"zoom": 1.25, "x": 10.0, "y": -4.0}}
    assert admin.post("/api/topology/views", json={"name": "Kube networks"}).status_code == 422
    assert admin.put(f"/api/topology/views/{v['uid']}", json={"name": "K8s"}).get_json()["config"]["view"] == "l3"  # a rename keeps the rest
    viewer = make_user("v", role="viewer")
    assert [x["name"] for x in viewer.get("/api/topology/views").get_json()["views"]] == ["K8s"]
    assert viewer.post("/api/topology/views", json={"name": "mine"}).status_code == 403
    assert admin.delete(f"/api/topology/views/{v['uid']}").status_code == 204
    assert admin.get("/api/topology/views").get_json()["views"] == []


def test_cable_inside_one_host_survives_import(admin):
    cable(admin, "net-sw-core-01", "ge-0/0/46", "net-sw-core-01", "ge-0/0/47", label="LOOP-1")
    inventory.import_dict(inventory.export_dict())
    c = next(c for c in host(admin, "net-sw-core-01")["cables"] if c["port"] in ("ge-0/0/46", "ge-0/0/47"))
    assert c["peer"] == "net-sw-core-01" and c["label"] == "LOOP-1"
