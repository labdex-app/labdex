def names(c, path="/api/hosts"):
    return {x["name"] for x in c.get(path).get_json()}


def test_scoped_editor(make_user):
    ed = make_user("ed", role="editor", scopes=["zone:DC_TC"])
    visible = names(ed)
    assert "srv-vm-dns-01" in visible and "sonos-living" not in visible
    assert ed.get("/api/hosts/sonos-living").status_code == 404
    # the edge firewall lives in DC_HT but has an address in srv (DC_TC): visible, not editable
    fw = ed.get("/api/hosts/net-fw-edge-01").get_json()
    assert "net-fw-edge-01" in visible and fw["can_edit"] is False
    assert ed.put("/api/hosts/net-fw-edge-01", json={**fw, "function": "mine now"}).status_code == 403
    h = ed.get("/api/hosts/srv-vm-dns-01").get_json()
    assert h["can_edit"]
    h["function"] = "Resolver"
    assert ed.put("/api/hosts/srv-vm-dns-01", json=h).status_code == 200
    # can't move it out of the scope
    assert ed.put("/api/hosts/srv-vm-dns-01", json={**h, "zone": "DC_HT"}).status_code == 403
    # can't create outside the scope, can inside
    assert ed.post("/api/hosts", json={"name": "cli-pc-x-01", "zone": "CA_ST"}).status_code == 403
    assert ed.post("/api/hosts", json={"name": "srv-vm-x-01", "zone": "DC_TC"}).status_code == 201
    # networks: editor role, scope DC_TC
    assert names(ed, "/api/networks") == {"srv", "k8s"}


def test_viewer_cannot_write(make_user):
    v = make_user("vic", role="viewer")
    assert len(names(v)) == 15
    assert v.delete("/api/hosts/sonos-living").status_code == 403
    assert v.get("/api/admin/users").status_code == 403


def test_object_grant_without_roles(make_user):
    g = make_user("gina", grants=[{"object_type": "host", "object_name": "sonos-*", "actions": ["edit"]}])
    assert names(g) == {"sonos-living"}
    h = g.get("/api/hosts/sonos-living").get_json()
    assert g.put("/api/hosts/sonos-living", json={**h, "function": "Speaker 2"}).status_code == 200
    assert g.get("/api/networks").get_json() == []


def test_group_binding_and_network_scope(make_user):
    c = make_user("carl", role="viewer", scopes=["network:main"], group="family")
    assert names(c) == {"net-fw-edge-01", "cli-pc-admin-01", "cli-nb-admin-01"}
    assert names(c, "/api/networks") == {"main"}


def test_tag_and_name_scopes(make_user):
    t = make_user("tara", role="viewer", scopes=["tag:admin", "name:srv-vm-k3s-*"])
    assert names(t) == {"cli-pc-admin-01", "cli-nb-admin-01", "srv-vm-k3s-01", "srv-vm-k3s-02"}


def test_alias_scope(make_user):
    a = make_user("al", role="editor", scopes=["zone:CA_*"])
    visible = names(a, "/api/aliases")
    assert "NG_ZONE_CA_ST" in visible and "N4_ZONE_DC_HT_MGMT" not in visible
    assert "NG_ZONE_CA" not in visible  # whole-area group needs an area: scope
    assert a.post("/api/aliases", json={"name": "HG_ZONE_CA_ST_PRINTERS", "type": "host"}).status_code == 201
    assert a.post("/api/aliases", json={"name": "HG_SPECIAL_X", "type": "host"}).status_code == 403


def test_history_and_lint_filtered(make_user, admin):
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "role": "x"})
    h = admin.get("/api/hosts/sonos-living").get_json()
    admin.put("/api/hosts/sonos-living", json={**h, "role": "y"})
    v = make_user("val", role="viewer", scopes=["zone:DC_TC"])
    seen = {e["object_name"] for e in v.get("/api/history").get_json()}
    assert "srv-vm-dns-01" in seen and "sonos-living" not in seen and "root" not in seen
    assert {e["object_name"] for e in admin.get("/api/history").get_json()} >= {"srv-vm-dns-01", "sonos-living"}


def test_permission_summary(make_user):
    o = make_user("op", role="operator")
    me = o.get("/api/auth/me").get_json()
    assert "apply" in me["permissions"]["integration"] and "edit" not in me["permissions"]["host"]


def test_custom_role_validation(admin):
    assert admin.post("/api/admin/roles", json={"name": "x", "permissions": ["host:fly"]}).status_code == 422
    assert admin.post("/api/admin/roles", json={"name": "dns-editor", "permissions": ["host:edit"]}).status_code == 201
    assert admin.post("/api/admin/bindings", json={"user": "root", "role": "dns-editor", "scopes": ["zone DC"]}).status_code == 422
    builtin = next(r for r in admin.get("/api/admin/roles").get_json()["roles"] if r["name"] == "viewer")
    assert admin.delete(f"/api/admin/roles/{builtin['id']}").status_code == 409
