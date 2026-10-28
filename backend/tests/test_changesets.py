"""Change sets: a batch of inventory changes reviewed as one unit, then applied all or nothing."""
import yaml

from labdex import changesets
from labdex.models import AuditEvent, Host, Network, db

NEW_NET = """
networks:
  - {name: guest2, vlan: 232, zone: CA_UT, prefix4: 10.10.232.0/24, gateway4: 10.10.232.254}
hosts:
  - name: cli-lt-ana-01
    zone: CA_UT
    interfaces: [{network: guest2, ip4: 10.10.232.5, primary: true}]
aliases:
  - {name: GUEST_TWO_HOSTS, type: host, members: [cli-lt-ana-01]}
"""


def make(c, text, name="guest vlan 2"):
    r = c.post("/api/changesets", json={"name": name, "description": "second guest network", "yaml": text})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["uid"]


def names(model):
    return {x.name for x in db.session.scalars(db.select(model))}


def test_preview_shows_effects_and_keeps_nothing(admin):
    uid = make(admin, NEW_NET)
    r = admin.post(f"/api/changesets/{uid}/preview")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] and not d["applied"]
    assert [(o["type"], o["name"], o["action"]) for o in d["operations"]] == [("network", "guest2", "create"), ("host", "cli-lt-ana-01", "create"), ("alias", "GUEST_TWO_HOSTS", "create")]
    assert any("GUEST_TWO_HOSTS" == a for a in d["aliases"]["added"]) and any(a.startswith("N4_") for a in d["aliases"]["added"])
    assert any("cli-lt-ana-01" in x for x in d["dns"]["added"])
    assert "guest2" not in names(Network) and "cli-lt-ana-01" not in names(Host)       # a preview keeps nothing
    assert admin.get("/api/changesets").get_json()[0]["status"] == "draft"


def test_apply_is_one_unit_with_history_and_cannot_repeat(admin):
    uid = make(admin, NEW_NET)
    r = admin.post(f"/api/changesets/{uid}/apply")
    assert r.status_code == 200 and r.get_json()["applied"], r.get_json()
    assert "guest2" in names(Network) and "cli-lt-ana-01" in names(Host)
    h = admin.get("/api/hosts/cli-lt-ana-01").get_json()
    assert h["zone"] == "CA_UT" and h["interfaces"][0]["ip4"] == "10.10.232.5"
    events = db.session.scalars(db.select(AuditEvent).where(AuditEvent.summary.like("change set%"))).all()
    assert {(e.object_type, e.object_name) for e in events} == {("network", "guest2"), ("host", "cli-lt-ana-01"), ("alias", "GUEST_TWO_HOSTS")}
    assert all("guest vlan 2" in e.summary for e in events)
    cs = admin.get(f"/api/changesets/{uid}").get_json()
    assert cs["status"] == "applied" and cs["applied_by"] == "root"
    assert admin.post(f"/api/changesets/{uid}/apply").status_code == 409
    assert admin.put(f"/api/changesets/{uid}", json={"name": "x"}).status_code == 409
    assert admin.delete(f"/api/changesets/{uid}").status_code == 409


def test_one_bad_change_saves_nothing(admin):
    bad = NEW_NET.replace("aliases:", "  - {name: cli-lt-bob-01, zone: CA_UT, interfaces: [{network: nowhere, ip4: 10.99.0.5}]}\naliases:")
    uid = make(admin, bad, "bad")
    r = admin.post(f"/api/changesets/{uid}/apply")
    assert r.status_code == 422
    d = r.get_json()
    assert not d["ok"] and not d["applied"]
    errs = {o["name"]: o["errors"] for o in d["operations"] if o["errors"]}
    assert list(errs) == ["cli-lt-bob-01"] and "nowhere" in " ".join(errs["cli-lt-bob-01"])
    assert "guest2" not in names(Network) and "cli-lt-ana-01" not in names(Host)          # the good ones weren't kept either
    assert admin.get(f"/api/changesets/{uid}").get_json()["status"] == "draft"


def test_update_lists_fields_and_new_lint_findings(admin):
    other = next(i["ip4"] for h in admin.get("/api/hosts").get_json() if h["name"] != "srv-vm-dns-01" for i in h["interfaces"] if i["network"] == "srv" and i.get("ip4"))
    uid = make(admin, f"hosts:\n  - name: srv-vm-dns-01\n    function: Resolver v2\n    interfaces: [{{network: srv, ip4: {other}, primary: true}}]\n", "move dns")
    d = admin.post(f"/api/changesets/{uid}/preview").get_json()
    op = d["operations"][0]
    assert op["action"] == "update" and "function" in op["fields"] and any(f.startswith("interfaces") for f in op["fields"])
    assert any(i["code"] == "duplicate-ip" for i in d["lint"]["new"])
    assert admin.get("/api/hosts/srv-vm-dns-01").get_json()["function"] == "DNS resolver"  # untouched


def test_delete_a_host_then_its_network_in_one_set(admin):
    hosts_on = sorted({h["name"] for h in admin.get("/api/hosts").get_json() if any(i["network"] == "dmz" for i in h["interfaces"])})
    assert hosts_on
    alone = make(admin, "delete: {networks: [dmz]}", "just the network")
    d = admin.post(f"/api/changesets/{alone}/preview").get_json()
    assert not d["ok"] and "still use it" in d["operations"][0]["errors"][0]
    tidy = make(admin, yaml.safe_dump({"delete": {"hosts": hosts_on, "networks": ["dmz"]}}), "retire dmz")
    r = admin.post(f"/api/changesets/{tidy}/apply")
    assert r.status_code == 200, r.get_json()
    assert "dmz" not in names(Network) and not (set(hosts_on) & names(Host))


def test_shape_errors_are_explained(admin):
    for text, field in (("hostz: []", "hostz"), ("hosts: [{zone: DC_TC}]", "hosts[0]"), ("delete: {servers: [a]}", "delete"), ("- a\n- b", "patch"), ("a: [", "patch")):
        r = admin.post("/api/changesets", json={"name": "x", "yaml": text})
        assert r.status_code == 422 and r.get_json()["errors"][0]["field"] == field, (text, r.get_json())
    assert admin.post("/api/changesets", json={"name": "", "yaml": "hosts: []"}).status_code == 422
    uid = make(admin, "hosts: [{name: a-b-c-01, zone: DC_TC}]", "dup")
    assert admin.post("/api/changesets", json={"name": "dup", "yaml": "hosts: []"}).status_code == 422
    assert admin.put(f"/api/changesets/{uid}", json={"yaml": "nonsense: 1"}).status_code == 422


def test_edit_and_delete_a_draft(admin):
    uid = make(admin, "hosts: [{name: a-b-c-01, zone: DC_TC}]", "draft")
    r = admin.put(f"/api/changesets/{uid}", json={"name": "renamed", "description": "d", "yaml": NEW_NET})
    assert r.status_code == 200 and r.get_json()["counts"] == {"networks": 1, "hosts": 1, "aliases": 1, "deletes": 0}
    assert admin.delete(f"/api/changesets/{uid}").status_code == 204
    assert admin.get(f"/api/changesets/{uid}").status_code == 404


def test_permissions_are_checked_per_object(admin, make_user):
    ed = make_user("ed", role="editor", scopes=["zone:CA_*"])
    uid = make(ed, "hosts:\n  - {name: srv-vm-dns-01, function: hijacked}\n  - {name: cli-lt-ana-01, zone: CA_UT}\n", "ed set")
    d = ed.post(f"/api/changesets/{uid}/apply").get_json()
    assert not d["ok"]
    by = {o["name"]: o for o in d["operations"]}
    assert "may not edit" in by["srv-vm-dns-01"]["errors"][0] and not by["cli-lt-ana-01"]["errors"]
    assert admin.get("/api/hosts/srv-vm-dns-01").get_json()["function"] == "DNS resolver"
    viewer = make_user("vi", role="viewer")
    assert viewer.get("/api/changesets").status_code == 403


def test_engine_directly_without_a_principal(loaded):
    patch = changesets.parse(NEW_NET)
    out = changesets.run(patch, "direct", apply=True)
    assert out["ok"] and out["applied"] and "guest2" in names(Network)
    again = changesets.run(changesets.parse("delete: {hosts: [nobody]}"))
    assert not again["ok"] and "no host" in again["operations"][0]["errors"][0]


def test_cli_previews_then_applies(loaded, tmp_path):
    from flask.testing import FlaskCliRunner
    f = tmp_path / "guest.yaml"
    f.write_text(NEW_NET, encoding="utf-8")
    runner = FlaskCliRunner(loaded)
    out = runner.invoke(args=["changeset", str(f)])
    assert out.exit_code == 0 and "preview only" in out.output and "guest2" not in names(Network)
    out = runner.invoke(args=["changeset", str(f), "--apply"])
    assert out.exit_code == 0 and "applied" in out.output and "guest2" in names(Network)
    f.write_text("hosts:\n  - {name: x-y-z-01, zone: CA_UT, interfaces: [{network: nowhere, ip4: 10.99.0.5}]}\n", encoding="utf-8")
    out = runner.invoke(args=["changeset", str(f), "--apply"])
    assert out.exit_code == 1 and "nowhere" in out.output
