"""1.14: racks and where hosts sit in them."""
from labdex import inventory

from .test_v03 import host


def rack(admin, name="R1", units=12, **kw):
    r = admin.post("/api/racks", json={"name": name, "units": units, **kw})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_racks_and_placing_hosts(admin):
    r = rack(admin, site="Basement")
    assert r["units"] == 12 and r["hosts"] == [] and r["uid"]
    sw = host(admin, "net-sw-core-01")
    resp = admin.put("/api/hosts/net-sw-core-01", json={**sw, "rack": "R1", "rack_unit": 12, "rack_height": 1})
    assert resp.status_code == 200, resp.get_json()
    h = host(admin, "net-sw-core-01")
    assert (h["rack"], h["rack_unit"], h["rack_height"], h["rack_face"]) == ("R1", 12, 1, "front")
    # a 2 U server at U10–11 by the rack page's endpoint
    resp = admin.put(f"/api/racks/{r['uid']}/hosts/srv-pm-pve-01", json={"unit": 10, "height": 2})
    assert resp.status_code == 200, resp.get_json()
    d = resp.get_json()
    assert [(x["name"], x["unit"], x["height"]) for x in d["hosts"]] == [("net-sw-core-01", 12, 1), ("srv-pm-pve-01", 10, 2)]
    assert d["used"] == 3
    # overlaps are refused, the rear is free, full depth clashes with both
    resp = admin.put(f"/api/racks/{r['uid']}/hosts/srv-pm-pve-02", json={"unit": 11, "height": 1})
    assert resp.status_code == 422 and "holds srv-pm-pve-01" in str(resp.get_json())
    assert admin.put(f"/api/racks/{r['uid']}/hosts/srv-pm-pve-02", json={"unit": 11, "height": 1, "face": "rear"}).status_code == 200
    resp = admin.put(f"/api/racks/{r['uid']}/hosts/net-fw-edge-01", json={"unit": 12, "height": 1, "face": "full"})
    assert resp.status_code == 422
    for bad in ({"unit": 12, "height": 2}, {"unit": 0}, {"unit": 5, "face": "side"}):
        assert admin.put(f"/api/racks/{r['uid']}/hosts/net-fw-edge-01", json=bad).status_code == 422, bad
    # out again
    assert admin.delete(f"/api/racks/{r['uid']}/hosts/srv-pm-pve-02").status_code == 200
    assert host(admin, "srv-pm-pve-02")["rack"] == ""
    hist = admin.get("/api/history?object_name=srv-pm-pve-01").get_json()
    assert "in rack R1 U10" in hist[0]["summary"]


def test_rack_rules(admin, make_user):
    r = rack(admin)
    assert admin.post("/api/racks", json={"name": "R1"}).status_code == 422  # exists
    assert admin.post("/api/racks", json={"name": "x", "units": 99}).status_code == 422
    admin.put(f"/api/racks/{r['uid']}/hosts/srv-pm-pve-01", json={"unit": 10, "height": 2})
    assert admin.put(f"/api/racks/{r['uid']}", json={"name": "R1", "units": 8}).status_code == 422  # a host sits higher
    assert admin.put(f"/api/racks/{r['uid']}", json={"name": "Rack A", "units": 16}).status_code == 200
    assert host(admin, "srv-pm-pve-01")["rack"] == "Rack A"
    v = make_user("v", role="viewer")
    assert v.get("/api/racks").get_json()["can_edit"] is False
    assert v.post("/api/racks", json={"name": "mine"}).status_code == 403
    assert v.put(f"/api/racks/{r['uid']}/hosts/srv-pm-pve-01", json={"unit": 1}).status_code == 403
    scoped = make_user("c", role="viewer", scopes=["zone:CA_*"])
    assert scoped.get(f"/api/racks/{r['uid']}").get_json()["hosts"] == []  # hosts by their rights
    assert admin.delete(f"/api/racks/{r['uid']}").status_code == 204
    h = host(admin, "srv-pm-pve-01")
    assert h["rack"] == "" and h["rack_unit"] is None


def test_racks_survive_export_and_import(admin):
    r = rack(admin, name="R2", units=24, site="Garage")
    admin.put(f"/api/racks/{r['uid']}/hosts/net-sw-core-01", json={"unit": 24, "height": 1, "face": "rear"})
    data = inventory.export_dict()
    assert data["racks"] == [{"name": "R2", "site": "Garage", "units": 24}]
    sw = next(h for h in data["hosts"] if h["name"] == "net-sw-core-01")
    assert (sw["rack"], sw["rack_unit"], sw["rack_face"]) == ("R2", 24, "rear")
    assert "rack" not in next(h for h in data["hosts"] if h["name"] == "srv-vm-dns-01")
    inventory.import_dict(data)
    assert host(admin, "net-sw-core-01")["rack_unit"] == 24


def test_panels_and_the_host_size(admin):
    r = rack(admin, name="R3", units=10)
    u = r["uid"]
    resp = admin.post(f"/api/racks/{u}/items", json={"kind": "patch", "label": "PP-A", "description": "24 × Cat6 to the office", "unit": 10})
    assert resp.status_code == 201, resp.get_json()
    item = resp.get_json()["items"][0]
    assert item["kind"] == "patch" and item["label"] == "PP-A" and item["unit"] == 10
    assert admin.post(f"/api/racks/{u}/items", json={"kind": "cables", "unit": 9}).status_code == 201
    # a panel takes its unit: a host can't go there, nor can another panel; the rear stays free
    sw = host(admin, "net-sw-core-01")
    resp = admin.put("/api/hosts/net-sw-core-01", json={**sw, "rack": "R3", "rack_unit": 10})
    assert resp.status_code == 422 and "holds PP-A" in str(resp.get_json())
    assert admin.post(f"/api/racks/{u}/items", json={"kind": "blank", "unit": 9}).status_code == 422
    assert admin.post(f"/api/racks/{u}/items", json={"kind": "blank", "unit": 9, "face": "rear"}).status_code == 201
    for bad in ({"kind": "toaster", "unit": 1}, {"kind": "blank", "unit": 11}, {"kind": "blank"}):
        assert admin.post(f"/api/racks/{u}/items", json=bad).status_code == 422, bad
    # the size is the host's own: set without a rack, used when placing, kept when it leaves
    resp = admin.put("/api/hosts/srv-pm-pve-01", json={**host(admin, "srv-pm-pve-01"), "rack_height": 2})
    assert resp.status_code == 200 and host(admin, "srv-pm-pve-01")["rack_height"] == 2
    d = admin.put(f"/api/racks/{u}/hosts/srv-pm-pve-01", json={"unit": 1}).get_json()
    assert next(h for h in d["hosts"] if h["name"] == "srv-pm-pve-01")["height"] == 2
    admin.delete(f"/api/racks/{u}/hosts/srv-pm-pve-01")
    assert host(admin, "srv-pm-pve-01")["rack_height"] == 2
    # edit, export, delete
    iid = item["id"]
    assert admin.put(f"/api/racks/{u}/items/{iid}", json={"kind": "patch", "label": "PP-A", "unit": 8, "height": 1}).status_code == 200
    data = inventory.export_dict()
    r3 = next(x for x in data["racks"] if x["name"] == "R3")
    assert {"kind": "patch", "label": "PP-A", "unit": 8, "height": 1} in r3["items"]
    assert admin.delete(f"/api/racks/{u}/items/{iid}").status_code == 200
    inventory.import_dict(data)
    assert len(admin.get("/api/racks/R3").get_json()["items"]) == 3


def test_an_import_keeps_the_ids(admin):
    before = host(admin, "srv-vm-dns-01")["uid"]
    net = admin.get("/api/networks/srv").get_json()["uid"]
    inventory.import_dict(inventory.export_dict())
    assert host(admin, "srv-vm-dns-01")["uid"] == before
    assert admin.get("/api/networks/srv").get_json()["uid"] == net
