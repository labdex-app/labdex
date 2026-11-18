"""labdex plan: what importing a file would change, for a merge request, without keeping anything."""
import json

import yaml

from labdex import fileplan, inventory
from labdex.models import Host, db


def _count():
    return db.session.scalar(db.select(db.func.count()).select_from(Host))


def test_no_changes(admin):
    before = _count()
    p = fileplan.plan(inventory.export_yaml())
    assert p["ok"] and fileplan.empty(p), p["inventory"]
    assert fileplan.text(p).startswith("No changes")
    assert _count() == before


def test_a_changed_host_shows_up_and_nothing_is_kept(admin):
    data = yaml.safe_load(inventory.export_yaml())
    host = next(h for h in data["hosts"] if h["name"] == "srv-vm-dns-01")
    old_ip = host["interfaces"][0]["ip4"]
    host["interfaces"][0]["ip4"] = "10.10.100.199"
    data["hosts"] = [h for h in data["hosts"] if h["name"] != "srv-vm-mon-01"]
    before = _count()
    p = fileplan.plan(yaml.safe_dump(data))
    assert p["ok"]
    changes = {(c["op"], c["name"]) for c in p["inventory"]}
    assert ("update", "srv-vm-dns-01") in changes and ("delete", "srv-vm-mon-01") in changes
    upd = next(c for c in p["inventory"] if c["name"] == "srv-vm-dns-01")
    assert {"path": "interfaces[0].ip4", "before": old_ip, "after": "10.10.100.199"} in upd["fields"]
    dns = p["generated"]["dns"]
    assert any("10.10.100.199" in r for r in dns["add"]) and any(old_ip in r for r in dns["remove"])
    assert any(a["name"] for a in p["generated"]["aliases"])
    # nothing was kept, and generated output is back to the database's
    assert _count() == before
    assert db.session.scalar(db.select(Host).where(Host.name == "srv-vm-dns-01")).interfaces[0].ip4 == old_ip
    assert not any("10.10.100.199" in r["value"] for r in admin.get("/api/dns").get_json()["generated"])
    md = fileplan.markdown(p)
    assert "srv-vm-dns-01" in md and "```diff" in md and "| - | host | `srv-vm-mon-01` |" in md


def test_errors_are_reported(admin):
    data = yaml.safe_load(inventory.export_yaml())
    data["hosts"].append({"name": "Bad Name", "zone": "SRV"})
    p = fileplan.plan(yaml.safe_dump(data))
    assert not p["ok"] and p["errors"] and p["errors"][0]["file"] == "new"
    assert "can't be imported" in fileplan.markdown(p)


def test_with_a_base_and_a_site_file(admin):
    """CI: base files from the target branch first, then the merge request's site file."""
    admin.post("/api/sites", json={"code": "public", "name": "Public"})
    admin.post("/api/networks", json={"name": "contabo", "site": "public", "zone": "DMZ_TC", "prefix4": "203.0.113.0/24"})
    base_full = inventory.export_yaml()
    site_file = yaml.safe_load(admin.get("/api/sites/public/export").get_data(as_text=True))
    site_file["hosts"] = [{"name": "srv-vm-oak-01", "zone": "DMZ_TC", "function": "k3s node",
                           "interfaces": [{"network": "contabo", "ip4": "203.0.113.11", "primary": True}]}]
    # wipe the database's public site: the plan must come from the base, not from here
    admin.delete("/api/networks/contabo")
    p = fileplan.plan(yaml.safe_dump(site_file), [base_full])
    assert p["ok"], p["errors"]
    assert p["site"] == "public"
    assert [(c["op"], c["kind"], c["name"]) for c in p["inventory"]] == [("create", "host", "srv-vm-oak-01")]
    assert any("203.0.113.11" in r for r in p["generated"]["dns"]["add"])
    assert not p["generated"]["dns"]["remove"]  # home records aren't part of a site's plan


def test_a_new_site(admin):
    p = fileplan.plan(yaml.safe_dump({"site": "lab2", "networks": [], "hosts": []}))
    assert p["ok"] and fileplan.empty(p)
    from labdex import sites
    assert not sites.exists("lab2")


def test_api(admin):
    data = yaml.safe_load(inventory.export_yaml())
    data["hosts"] = [h for h in data["hosts"] if h["name"] != "srv-vm-mon-01"]
    r = admin.post("/api/admin/plan", json={"yaml": yaml.safe_dump(data)})
    assert r.status_code == 200
    out = r.get_json()
    assert out["summary"]["delete"] == 1 and not out["empty"] and "srv-vm-mon-01" in out["markdown"]


def test_cli(loaded, tmp_path):
    runner = loaded.test_cli_runner()
    f = tmp_path / "inv.yaml"
    with loaded.app_context():
        data = yaml.safe_load(inventory.export_yaml())
    f.write_text(yaml.safe_dump(data))
    r = runner.invoke(args=["plan", str(f), "--detailed-exitcode"])
    assert r.exit_code == 0 and "No changes" in r.output, r.output
    data["hosts"] = data["hosts"][1:]
    f.write_text(yaml.safe_dump(data))
    r = runner.invoke(args=["plan", str(f), "--detailed-exitcode"])
    assert r.exit_code == 2 and "1 to remove" in r.output, r.output
    r = runner.invoke(args=["plan", str(f), "--format", "json"])
    assert r.exit_code == 0 and json.loads(r.output)["summary"]["delete"] == 1
    r = runner.invoke(args=["plan", str(f), "--format", "markdown"])
    assert r.output.startswith("### labdex plan")


def test_cli_base_ref(loaded, tmp_path):
    import subprocess
    runner = loaded.test_cli_runner()
    repo = tmp_path / "repo"
    (repo / "labdex").mkdir(parents=True)
    f = repo / "labdex" / "inventory.yaml"
    with loaded.app_context():
        data = yaml.safe_load(inventory.export_yaml())
    f.write_text(yaml.safe_dump(data))

    def git(*a):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "base")
    data["hosts"] = data["hosts"][1:]
    f.write_text(yaml.safe_dump(data))
    r = runner.invoke(args=["plan", str(f), "--base-ref", "main", "--detailed-exitcode"])
    assert r.exit_code == 2 and "1 to remove" in r.output, r.output
    r = runner.invoke(args=["plan", str(f), "--base-ref", "no-such-ref"])  # a file new on its branch: no base
    assert r.exit_code == 0, r.output
