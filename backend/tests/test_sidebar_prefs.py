"""Your sidebar: pins, hidden pages and order are kept per user."""


def test_sidebar_roundtrip_and_per_user(admin, make_user):
    assert admin.get("/api/me/sidebar").get_json() == {"pins": [], "hidden": [], "order": [], "from_default": True}
    pins = [{"to": "/hosts/abc", "kind": "host", "label": "web-01", "key": "web-01"}]
    r = admin.put("/api/me/sidebar", json={"pins": pins, "hidden": ["/racks"], "order": ["/networks", "/hosts", "/hosts"]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"pins": pins, "hidden": ["/racks"], "order": ["/networks", "/hosts"], "from_default": False}
    assert admin.get("/api/me/sidebar").get_json()["pins"] == pins
    bob = make_user("bob", role="viewer")
    assert bob.get("/api/me/sidebar").get_json()["pins"] == []  # only for you


def test_sidebar_validation(admin):
    bad = [{"to": "https://evil.example.com", "kind": "host"}, {"to": "//evil", "kind": "page"}, {"to": "/x", "kind": "nope"}]
    r = admin.put("/api/me/sidebar", json={"pins": bad})
    assert r.status_code == 422 and len(r.get_json()["errors"]) == 3
    assert admin.put("/api/me/sidebar", json={"pins": [{"to": f"/hosts/{n}", "kind": "host"} for n in range(31)]}).status_code == 422
    assert admin.put("/api/me/sidebar", json={"hidden": "nope"}).status_code == 422
    assert admin.get("/api/me/sidebar").get_json()["pins"] == []


def test_admin_default_pages_until_you_change_yours(admin, make_user):
    bob = make_user("bob", role="viewer")
    assert bob.put("/api/admin/sidebar-default", json={"hidden": ["/racks"]}).status_code == 403
    r = admin.put("/api/admin/sidebar-default", json={"hidden": ["/racks"], "order": ["/networks", "/hosts"]})
    assert r.status_code == 200, r.get_json()
    d = bob.get("/api/me/sidebar").get_json()
    assert d["hidden"] == ["/racks"] and d["order"] == ["/networks", "/hosts"] and d["from_default"] is True
    # pinning while following the default keeps following it
    pin = {"to": "/hosts/abc", "kind": "host", "label": "web-01"}
    d = bob.put("/api/me/sidebar", json={"pins": [pin], "hidden": [], "order": [], "from_default": True}).get_json()
    assert d["from_default"] is True and d["hidden"] == ["/racks"] and len(d["pins"]) == 1
    admin.put("/api/admin/sidebar-default", json={"hidden": ["/dns"]})
    assert bob.get("/api/me/sidebar").get_json()["hidden"] == ["/dns"]
    # your own pages win; a reset follows the default again and keeps your pins
    assert bob.put("/api/me/sidebar", json={"pins": [pin], "hidden": ["/patches"]}).get_json()["hidden"] == ["/patches"]
    admin.put("/api/admin/sidebar-default", json={"hidden": ["/topology"]})
    assert bob.get("/api/me/sidebar").get_json()["hidden"] == ["/patches"]
    d = bob.delete("/api/me/sidebar").get_json()
    assert d["hidden"] == ["/topology"] and d["from_default"] is True and len(d["pins"]) == 1
    assert admin.put("/api/admin/sidebar-default", json={"hidden": ["https://x"]}).status_code == 422
