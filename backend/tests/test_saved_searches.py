"""Saved searches of the Hosts page, and bulk edits of features and zone."""


def save(c, name, shared=False, **query):
    return c.post("/api/searches", json={"name": name, "shared": shared, "query": query or {"q": "tag:k3s"}})


def test_save_list_update_delete(admin):
    r = save(admin, "k3s nodes", status="active", q="tag:k3s")
    assert r.status_code == 201, r.get_json()
    s = r.get_json()
    assert s["query"] == {"q": "tag:k3s", "status": "active"} and s["can_edit"] and not s["shared"]
    assert [x["name"] for x in admin.get("/api/searches").get_json()["searches"]] == ["k3s nodes"]

    assert save(admin, "k3s nodes").status_code == 422                       # same name twice
    assert admin.post("/api/searches", json={"name": "empty", "query": {}}).status_code == 422
    assert admin.post("/api/searches", json={"name": "", "query": {"q": "x"}}).status_code == 422

    r = admin.put(f"/api/searches/{s['uid']}", json={"name": "Kube", "query": {"zone": "DC_TC", "bogus": "dropped"}})
    assert r.status_code == 200 and r.get_json()["query"] == {"zone": "DC_TC"}
    assert admin.delete(f"/api/searches/{s['uid']}").status_code == 204
    assert admin.get("/api/searches").get_json()["searches"] == []


def test_own_and_shared(admin, make_user):
    ed = make_user("ed", role="editor")
    assert save(ed, "mine").status_code == 201
    assert save(ed, "for all", shared=True).status_code == 403                  # only administrators share
    assert save(admin, "everyone", shared=True).status_code == 201

    def names(c):
        return sorted(x["name"] for x in c.get("/api/searches").get_json()["searches"])

    assert names(ed) == ["everyone", "mine"]
    assert names(admin) == ["everyone"]                                          # not other people's own ones

    mine = next(x for x in ed.get("/api/searches").get_json()["searches"] if x["name"] == "mine")
    shared = next(x for x in ed.get("/api/searches").get_json()["searches"] if x["name"] == "everyone")
    assert admin.put(f"/api/searches/{mine['uid']}", json={"name": "x"}).status_code == 404
    assert ed.put(f"/api/searches/{shared['uid']}", json={"name": "x"}).status_code == 403
    assert ed.delete(f"/api/searches/{shared['uid']}").status_code == 403
    assert ed.delete(f"/api/searches/{mine['uid']}").status_code == 204


def test_bulk_features_and_zone(admin):
    f = "server"
    assert admin.post("/api/features", json={"path": f, "vars": {}}).status_code in (200, 201)
    names = ["srv-vm-dns-01", "srv-vm-k3s-01"]
    r = admin.post("/api/hosts/bulk", json={"names": names, "add_features": [f]})
    assert r.status_code == 200, r.get_json()
    for n in names:
        assert f in admin.get(f"/api/hosts/{n}").get_json()["features"]
    r = admin.post("/api/hosts/bulk", json={"names": names, "remove_features": [f]})
    assert r.status_code == 200
    for n in names:
        assert f not in admin.get(f"/api/hosts/{n}").get_json()["features"]
    assert admin.post("/api/hosts/bulk", json={"names": names, "add_features": ["no.such.feature"]}).status_code == 422

    before = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    r = admin.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01"], "zone": "DC_HT"})
    assert r.status_code == 200, r.get_json()
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    assert h["zone"] == "DC_HT" and h["zone_pinned"]
    r = admin.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01"], "zone": ""})
    assert r.status_code == 200
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    assert h["zone"] == before["zone"] and not h["zone_pinned"]
