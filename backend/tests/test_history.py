from labdex import audit


def test_update_diff_and_revert(admin):
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "function": "Changed", "interfaces": [{"network": "srv", "ip4": "10.10.100.54"}]})
    ev = admin.get("/api/history?object_name=srv-vm-dns-01").get_json()[0]
    assert ev["action"] == "update" and ev["actor"] == "root" and ev["revertible"]
    full = admin.get(f"/api/history/{ev['id']}").get_json()
    paths = {d["path"] for d in full["diff"]}
    assert "function" in paths and "interfaces[0].ip4" in paths
    r = admin.post(f"/api/history/{ev['id']}/revert")
    assert r.status_code == 200
    back = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    assert back["function"] == "DNS resolver" and back["interfaces"][0]["ip4"] == "10.10.100.53"


def test_revert_delete_and_create(admin):
    assert admin.delete("/api/hosts/srv-vm-k3s-02").status_code == 204
    ev = admin.get("/api/history?object_name=srv-vm-k3s-02&action=delete").get_json()[0]
    admin.post(f"/api/history/{ev['id']}/revert")
    assert admin.get("/api/hosts/srv-vm-k3s-02").get_json()["runs_on"] == "srv-pm-pve-01"

    admin.post("/api/hosts", json={"name": "srv-vm-tmp-01", "zone": "DC_TC"})
    ev = admin.get("/api/history?object_name=srv-vm-tmp-01").get_json()[0]
    admin.post(f"/api/history/{ev['id']}/revert")
    assert admin.get("/api/hosts/srv-vm-tmp-01").status_code == 404


def test_revision_moves_with_inventory_only(admin, loaded):
    r0 = audit.inventory_revision()
    admin.post("/api/auth/tokens", json={"name": "x"})
    assert audit.inventory_revision() == r0
    admin.post("/api/aliases", json={"name": "SG_X", "type": "port", "members": ["1"]})
    assert audit.inventory_revision() > r0


def test_settings_and_restore_are_audited(admin):
    assert admin.put("/api/admin/settings", json={"domain": "lab2.example.net"}).status_code == 200
    yaml_text = admin.get("/api/admin/backup").get_data(as_text=True)
    assert "lab2.example.net" in yaml_text
    assert admin.post("/api/admin/restore", json={"yaml": yaml_text}).status_code == 400
    assert admin.post("/api/admin/restore", json={"yaml": yaml_text, "confirm": "replace"}).status_code == 200
    actions = {e["action"] for e in admin.get("/api/history").get_json()}
    assert {"import", "update"} <= actions
