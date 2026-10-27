"""1.12: dashboards: cards on a grid, shared and personal; links that stay safe."""


def test_the_start_page_is_a_shared_dashboard(admin, make_user):
    d = admin.get("/api/dashboards").get_json()
    assert [x["name"] for x in d["dashboards"]] == ["Overview"] and d["dashboards"][0]["shared"]
    assert "links" in d["types"] and d["can_share"]
    uid = d["dashboards"][0]["uid"]
    items = admin.get(f"/api/dashboards/{uid}").get_json()["items"]
    assert {i["type"] for i in items} >= {"counts", "widgets", "approvals", "changes"}
    v = make_user("v", role="viewer")
    assert v.get(f"/api/dashboards/{uid}").get_json()["can_edit"] is False
    assert v.put(f"/api/dashboards/{uid}", json={"name": "mine now"}).status_code == 403
    assert v.post("/api/dashboards", json={"name": "x", "shared": True}).status_code == 403


def test_personal_dashboards_are_private(admin, make_user):
    v = make_user("v", role="viewer")
    r = v.post("/api/dashboards", json={"name": "Homelab links", "items": [
        {"type": "links", "x": 0, "y": 0, "w": 6, "h": 4, "config": {"title": "Media", "links": [
            {"name": "Jellyfin", "url": "https://jellyfin.lab.example.net", "description": "films"},
            {"name": "Hosts", "url": "/hosts"}]}},
        {"type": "services", "x": 6, "y": 0, "w": 6, "h": 4, "config": {"zone": "dc_tc"}},
        {"type": "note", "x": 0, "y": 4, "w": 12, "h": 2, "config": {"text": "Patch day is Tuesday"}}]})
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert not d["shared"] and d["can_edit"]
    assert d["items"][0]["config"]["links"][0] == {"name": "Jellyfin", "url": "https://jellyfin.lab.example.net", "description": "films"}
    assert d["items"][1]["config"]["zone"] == "DC_TC"
    assert all(i["id"] for i in d["items"])
    assert d["uid"] in {x["uid"] for x in v.get("/api/dashboards").get_json()["dashboards"]}
    assert d["uid"] not in {x["uid"] for x in admin.get("/api/dashboards").get_json()["dashboards"]}  # not even admins see it
    assert admin.get(f"/api/dashboards/{d['uid']}").status_code == 404
    items = d["items"] + [{"type": "counts", "x": 20, "y": -3, "w": 99, "h": 0}]
    r = v.put(f"/api/dashboards/{d['uid']}", json={"items": items, "name": "Links"})
    assert r.status_code == 200
    last = r.get_json()["items"][-1]
    assert (last["x"], last["y"], last["w"], last["h"]) == (0, 0, 12, 1)  # kept on the grid
    assert v.delete(f"/api/dashboards/{d['uid']}").status_code == 204


def test_links_must_be_safe(admin):
    for url in ("javascript:alert(1)", "data:text/html,<b>x</b>", "//evil.example", "/\\evil.example", "ftp://x", "https://"):
        r = admin.post("/api/dashboards", json={"name": "bad", "items": [
            {"type": "links", "x": 0, "y": 0, "w": 4, "h": 2, "config": {"links": [{"name": "x", "url": url}]}}]})
        assert r.status_code == 422 and "links[0].url" in str(r.get_json()), url
    for bad in ([{"type": "nope"}], "x", [{"type": "widget", "config": {}}], [{"type": "counts", "x": "a"}]):
        assert admin.post("/api/dashboards", json={"name": "bad", "items": bad}).status_code == 422, bad
    assert admin.post("/api/dashboards", json={"name": ""}).status_code == 422


def test_admins_share_dashboards(admin, make_user):
    r = admin.post("/api/dashboards", json={"name": "Rack room", "shared": True, "items": [{"type": "zones", "x": 0, "y": 0, "w": 4, "h": 3}]})
    assert r.status_code == 201
    v = make_user("v", role="viewer")
    names = [x["name"] for x in v.get("/api/dashboards").get_json()["dashboards"]]
    assert names[:2] == ["Overview", "Rack room"]


def test_history_without_sign_ins(admin):
    from labdex import audit
    from labdex.models import db
    audit.record("login", "user", "admin", actor=("admin", "user"))
    db.session.commit()
    assert any(e["action"] == "login" for e in admin.get("/api/history?limit=200").get_json())
    assert not any(e["action"] in ("login", "logout") for e in admin.get("/api/history?limit=200&changes=1").get_json())
