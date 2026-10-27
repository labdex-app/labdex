"""v0.9.0: the zone policy matrix."""
import yaml

from labdex import inventory, policy



def test_matrix_cells(admin, make_user):
    admin.post("/api/aliases", json={"name": "SG_WEB", "type": "port", "members": ["80", "443", "8000:8100"]})
    r = admin.put("/api/zone-policy", json={"src": "CA_ST", "dst": "DC_TC", "ports": ["SG_WEB", "TCP/22"], "description": "admins"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ports"] == ["SG_WEB", "tcp/22"]
    admin.put("/api/zone-policy", json={"src": "DC_HT", "dst": "DC_TC", "ports": ["any"]})
    d = admin.get("/api/zone-policy").get_json()
    assert {(c["src"], c["dst"]) for c in d["cells"]} == {("CA_ST", "DC_TC"), ("DC_HT", "DC_TC")} and "SG_WEB" in d["port_aliases"]
    # validation
    r = admin.put("/api/zone-policy", json={"src": "XX_YY", "dst": "DC_TC", "ports": ["any", "tcp/99999", "SG_NOPE"]})
    errs = {e["field"] for e in r.get_json()["errors"]}
    assert r.status_code == 422 and errs == {"src", "ports"}
    # matching
    assert policy.allows("CA_ST", "DC_TC", 8050) is True and policy.allows("CA_ST", "DC_TC", 22) is True
    assert policy.allows("CA_ST", "DC_TC", 3306) is False and policy.allows("DC_TC", "CA_ST", 22) is None
    assert policy.allows("DC_HT", "DC_TC", 1) is True and policy.allows("CA_ST", "DC_TC", 53, "udp") is False
    # removing a cell
    admin.put("/api/zone-policy", json={"src": "DC_HT", "dst": "DC_TC", "ports": []})
    assert len(admin.get("/api/zone-policy").get_json()["cells"]) == 1
    hist = admin.get("/api/history?object_type=zone_policy").get_json()
    assert hist[0]["summary"] == "DC_HT → DC_TC: no access" and hist[-1]["summary"] == "CA_ST → DC_TC: SG_WEB, tcp/22"
    # in the YAML, and back
    data = yaml.safe_load(inventory.export_yaml())
    assert data["zone_policy"] == [{"src": "CA_ST", "dst": "DC_TC", "ports": ["SG_WEB", "tcp/22"], "description": "admins"}]
    inventory.import_yaml(yaml.safe_dump(data))
    assert yaml.safe_load(inventory.export_yaml())["zone_policy"] == data["zone_policy"]
    # only administrators edit
    op = make_user("op", role="operator")
    assert op.get("/api/zone-policy").status_code == 200
    assert op.put("/api/zone-policy", json={"src": "CA_ST", "dst": "DC_TC", "ports": ["any"]}).status_code == 403
