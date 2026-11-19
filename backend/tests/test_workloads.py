"""Workloads: the compose integrations' stacks in one list, a compose file changed with a diff
first, recorded, and deployed only through the integration's plan."""
from labdex.models import Integration, db

from .test_compose_plugin import DOCKER, STACKS


def _compose(app, enabled=False):
    with app.app_context():
        db.session.add(Integration(plugin_id="compose", name="stacks", enabled=enabled, settings={"stacks": STACKS}))
        db.session.commit()


def test_list_and_read(admin, app):
    _compose(app)
    d = admin.get("/api/workloads").get_json()
    assert [(s["name"], s["host"]) for s in d["stacks"]] == [("grafana", DOCKER), ("whoami", "srv-vm-mon-01")]
    g = d["stacks"][0]
    assert g["services"][0] == {"name": "grafana", "image": "grafana/grafana:11.3.0", "ports": ["3000:3000"]}
    assert g["env"] == ["GF_ADMIN_PASSWORD", "GF_ADMIN_USER"] and g["can_edit"] and not g["can_plan"]  # not enabled
    one = admin.get(f"/api/workloads/stacks/{DOCKER}/grafana").get_json()
    assert one["compose"].startswith("services:\n  grafana:\n")
    assert admin.get("/api/workloads/stacks/nowhere/grafana").status_code == 404


def test_change_compose_with_dry_run(admin, app):
    _compose(app)
    url = f"/api/workloads/stacks/{DOCKER}/grafana"
    new = "services:\n  grafana:\n    image: grafana/grafana:11.4.0\n"
    d = admin.put(url, json={"compose": new, "dry_run": True}).get_json()
    assert "11.3.0" in d["before"] and "11.4.0" in d["after"]
    assert "11.3.0" in admin.get(url).get_json()["compose"]
    r = admin.put(url, json={"compose": new})
    assert r.status_code == 200 and "11.4.0" in r.get_json()["compose"]
    assert "11.4.0" in admin.get(url).get_json()["compose"]
    ev = admin.get("/api/history?type=integration&name=stacks").get_json()[0]
    assert "stack grafana" in ev["summary"]
    # the other stack and its env are kept
    with app.app_context():
        text = db.session.scalar(db.select(Integration).where(Integration.name == "stacks")).settings["stacks"]
    assert "whoami" in text and "vault:apps/grafana#admin" in text


def test_bad_compose_is_refused(admin, app):
    _compose(app)
    url = f"/api/workloads/stacks/{DOCKER}/grafana"
    assert admin.put(url, json={"compose": "services: [unclosed"}).status_code == 422
    assert admin.put(url, json={"compose": "services: {}"}).status_code == 422


def test_viewers_cannot_change(make_user, app):
    _compose(app)
    viewer = make_user("vic", role="viewer")
    assert viewer.get("/api/workloads").get_json()["stacks"][0]["can_edit"] is False
    assert viewer.put(f"/api/workloads/stacks/{DOCKER}/grafana", json={"compose": "services: {a: {image: x}}"}).status_code == 403
