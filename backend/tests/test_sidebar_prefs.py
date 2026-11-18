"""Your sidebar: pins, hidden pages and order are kept per user."""


def test_sidebar_roundtrip_and_per_user(admin, make_user):
    assert admin.get("/api/me/sidebar").get_json() == {"pins": [], "hidden": [], "order": []}
    pins = [{"to": "/hosts/abc", "kind": "host", "label": "web-01", "key": "web-01"}]
    r = admin.put("/api/me/sidebar", json={"pins": pins, "hidden": ["/racks"], "order": ["/networks", "/hosts", "/hosts"]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"pins": pins, "hidden": ["/racks"], "order": ["/networks", "/hosts"]}
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
