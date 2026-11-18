"""Quick edit beside the Hosts list: PATCH some fields, a dry run for the diff, the host's YAML."""
from labdex.models import Site, db


def test_patch_changes_only_the_given_fields(admin):
    full = admin.get("/api/hosts/srv-pm-pve-01").get_json()
    r = admin.patch("/api/hosts/srv-pm-pve-01", json={"function": "Main hypervisor", "tags": ["core"]})
    assert r.status_code == 200, r.get_json()
    h = r.get_json()
    assert h["function"] == "Main hypervisor" and h["tags"] == ["core"]
    assert h["fun"] == full["fun"] and len(h["interfaces"]) == len(full["interfaces"])
    hist = admin.get("/api/history?type=host&name=srv-pm-pve-01").get_json()
    assert hist and hist[0]["action"] == "update"


def test_patch_dry_run_saves_nothing(admin):
    r = admin.patch("/api/hosts/srv-pm-pve-01", json={"function": "Changed", "dry_run": True})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert "function: Changed" in d["after"] and "function: Changed" not in d["before"]
    assert d["site"] == "home" and d["git"] is None
    assert admin.get("/api/hosts/srv-pm-pve-01").get_json()["function"] != "Changed"


def test_patch_refuses_other_fields(admin):
    r = admin.patch("/api/hosts/srv-pm-pve-01", json={"interfaces": []})
    assert r.status_code == 422
    assert r.get_json()["errors"][0]["field"] == "interfaces"


def test_patch_checks_like_put(admin):
    r = admin.patch("/api/hosts/srv-pm-pve-01", json={"name": "Not A Name", "dry_run": True})
    assert r.status_code == 422
    assert admin.get("/api/hosts/srv-pm-pve-01").status_code == 200


def test_patch_zone_pins_and_empty_follows(admin):
    h = admin.patch("/api/hosts/srv-pm-pve-01", json={"zone": "DC_TC"}).get_json()
    assert h["zone"] == "DC_TC" and h["zone_pinned"] is True
    h = admin.patch("/api/hosts/srv-pm-pve-01", json={"zone": ""}).get_json()
    assert h["zone_pinned"] is False and h["zone"] == "DC_HT"


def test_patch_needs_edit_rights(make_user):
    viewer = make_user("vic", role="viewer")
    assert viewer.patch("/api/hosts/srv-pm-pve-01", json={"notes": "x"}).status_code == 403


def test_host_yaml_and_git_target(admin):
    d = admin.get("/api/hosts/srv-pm-pve-01/yaml").get_json()
    assert d["yaml"].startswith("- name: srv-pm-pve-01")
    assert d["git"] is None
    s = db.session.get(Site, "home")
    s.git = {"url": "https://git.example.com/lab.git", "branch": "main", "path": "labdex/home.yaml", "mode": "commit"}
    db.session.commit()
    assert admin.get("/api/hosts/srv-pm-pve-01/yaml").get_json()["git"] == {"branch": "main", "path": "labdex/home.yaml", "mode": "commit"}


def test_put_yaml_round_trips_and_saves(admin):
    y = admin.get("/api/hosts/srv-pm-pve-01/yaml").get_json()["yaml"]
    same = admin.put("/api/hosts/srv-pm-pve-01/yaml", json={"yaml": y, "dry_run": True}).get_json()
    assert same["before"] == same["after"]
    changed = y.replace("- name: srv-pm-pve-01\n", "- name: srv-pm-pve-01\n  notes: from yaml\n", 1)
    assert changed != y
    r = admin.put("/api/hosts/srv-pm-pve-01/yaml", json={"yaml": changed})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["notes"] == "from yaml"
    assert admin.get("/api/history?type=host&name=srv-pm-pve-01").get_json()[0]["action"] == "update"


def test_put_yaml_refuses_bad_yaml_and_cable_edits(admin):
    r = admin.put("/api/hosts/srv-pm-pve-01/yaml", json={"yaml": "- name: [unclosed"})
    assert r.status_code == 422 and "line" in r.get_json()["errors"][0]["message"]
    r = admin.put("/api/hosts/srv-pm-pve-01/yaml", json={"yaml": "- name: srv-pm-pve-01\n  cables: [{port: eth9, peer: x, peer_port: y}]\n"})
    assert r.status_code == 422 and "cables" in r.get_json()["errors"][0]["message"]
    r = admin.put("/api/hosts/srv-pm-pve-01/yaml", json={"yaml": "- just a string"})
    assert r.status_code == 422
