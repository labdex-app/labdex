"""v0.3: feature tree, secrets, host modules (DNS, firewall), Ansible runner, backups, upgrades."""
import shutil
import sqlite3
import textwrap

import pytest
import yaml

from labdex import backup
from labdex.models import Integration, db

from .conftest import ADMIN, client
from .fakes import FakeOPNsense, FakeS3, FakeTechnitium
from .test_plugins import make_instance, run


def host(c, hname):
    return c.get(f"/api/hosts/{hname}").get_json()


def put_host(c, hname, **changes):
    h = host(c, hname)
    r = c.put(f"/api/hosts/{hname}", json={**h, **changes})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ------------------------------------------------------------------ upgrade
def test_upgrade_adds_missing_columns(tmp_path):
    from labdex import create_app
    cfg = {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'u.db'}", "TESTING": True, "SECRET_KEY": "s",
           "LABDEX_PLUGIN_DIR": str(tmp_path / "p"), "LABDEX_SCHEDULER": False}
    create_app(cfg)
    con = sqlite3.connect(tmp_path / "u.db")
    con.execute("DROP TABLE alembic_version")  # a database from before 0.5: no Alembic yet
    con.execute("ALTER TABLE host DROP COLUMN features")
    con.execute("ALTER TABLE integration DROP COLUMN selectors")
    con.execute("INSERT INTO integration (plugin_id, name, enabled, settings, state, created_at) VALUES ('exports','old',0,'{}','{}','2026-01-01')")
    con.commit()
    con.close()
    app = create_app(cfg)
    with app.app_context():
        assert db.session.scalar(db.select(Integration)).selectors == ["*"]
    cols = [r[1] for r in sqlite3.connect(tmp_path / "u.db").execute("PRAGMA table_info(host)")]
    assert "features" in cols
    from labdex import schema
    with app.app_context(), db.engine.connect() as conn:
        assert schema.current(conn) == schema.head()


# ----------------------------------------------------------------- features
def make_tree(c):
    for f in [
        {"path": "server", "vars": "greeting: hello\nnested: {a: 1, b: 1}\npkgs: [vim]", "description": "basics"},
        {"path": "server.packages", "vars": {"pkgs": ["vim", "htop"]}},
        {"path": "server.packages.pve", "vars": {"nested": {"b": 2}}, "playbooks": ["write.yml"]},
        {"path": "kube", "vars": {"version": "1.30"}, "roles": ["noop"]},
        {"path": "kube.cluster.oak", "vars": {"version": "1.31", "vip": "10.10.128.10"}},
    ]:
        r = c.post("/api/features", json=f)
        assert r.status_code == 201, r.get_json()


def test_feature_tree_vars_and_inventory(admin):
    make_tree(admin)
    r = admin.put("/api/hosts/srv-vm-k3s-01", json={**host(admin, "srv-vm-k3s-01"), "features": ["nope"]})
    assert r.status_code == 422 and "unknown feature" in r.get_json()["errors"][0]["message"]
    d = put_host(admin, "srv-vm-k3s-01", features=["server.packages.pve", "kube.cluster.oak"], vars="greeting: hi k3s")
    assert d["features_expanded"] == ["server", "server.packages", "server.packages.pve", "kube", "kube.cluster", "kube.cluster.oak"]
    assert d["merged_vars"] == {"greeting": "hi k3s", "nested": {"a": 1, "b": 2}, "pkgs": ["vim", "htop"], "version": "1.31", "vip": "10.10.128.10"}

    f = admin.get("/api/features/kube.cluster.oak").get_json()
    assert [x["exists"] for x in f["chain"]] == [True, True, True] and f["hosts"] == ["srv-vm-k3s-01"]  # 1.16: the middle one is made too
    assert f["merged_vars"]["version"] == "1.31"

    inv = yaml.safe_load(admin.get("/api/integrations/x").get_data() or b"{}")
    make_instance(admin, "exports", "files", {})
    inv = yaml.safe_load(admin.get("/api/integrations/files/export/ansible_inventory").get_data(as_text=True))
    hv = inv["all"]["hosts"]["srv-vm-k3s-01"]
    assert hv["vip"] == "10.10.128.10" and hv["labdex_features"][-1] == "kube.cluster.oak"
    assert "srv-vm-k3s-01" in inv["all"]["children"]["feature_kube__cluster__oak"]["hosts"]
    assert "srv-vm-k3s-01" in inv["all"]["children"]["feature_server"]["hosts"]

    # rename follows hosts and children; delete in use is refused
    r = admin.put("/api/features/kube", json={**admin.get("/api/features/kube").get_json(), "path": "k8s"})
    assert r.status_code == 200
    assert host(admin, "srv-vm-k3s-01")["features"] == ["server.packages.pve", "k8s.cluster.oak"]
    assert admin.delete("/api/features/k8s").status_code == 422
    # survives a YAML round trip
    text = admin.get("/api/admin/backup").get_data(as_text=True)
    assert "k8s.cluster.oak" in text
    assert admin.post("/api/admin/restore", json={"yaml": text, "confirm": "replace"}).status_code == 200
    assert host(admin, "srv-vm-k3s-01")["vars"] == {"greeting": "hi k3s"}
    assert len(admin.get("/api/features").get_json()) == 6  # 1.16: k8s.cluster is made along the path


def test_feature_edit_needs_admin(make_user, admin):
    ed = make_user("ed", role="editor")
    assert ed.get("/api/features").status_code == 200
    assert ed.post("/api/features", json={"path": "x"}).status_code == 403


# ------------------------------------------------------------------ secrets
def test_global_secrets(make_user, admin, loaded, vault):
    assert admin.put("/api/secrets/opn-key", json={"value": "k", "description": "OPNsense API key"}).status_code == 201
    admin.put("/api/secrets/opn-secret", json={"value": "s"})
    lst = admin.get("/api/secrets").get_json()
    assert {s["name"] for s in lst} == {"opn-key", "opn-secret"} and all("value" not in s for s in lst)
    # 1.18: a global secret is the vault entry global/<name>, encrypted there
    from labdex.models import VaultEntry, VaultVersion
    e = db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == "global/opn-key"))
    assert e.fields == ["value"] and "OPNsense" in e.description
    assert db.session.scalar(db.select(VaultVersion).where(VaultVersion.entry_id == e.id)).ciphertext not in ("k", "")

    opn = FakeOPNsense()
    try:
        r = admin.post("/api/integrations", json={"plugin_id": "opnsense", "name": "fw"})
        r = admin.put("/api/integrations/fw", json={"enabled": True, "settings": {
            "url": opn.url, "verify_tls": False, "api_key": {"$ref": "opn-key"}, "api_secret": {"$ref": "nope"}}})
        assert r.status_code == 422
        r = admin.put("/api/integrations/fw", json={"enabled": True, "settings": {
            "url": opn.url, "verify_tls": False, "api_key": {"$ref": "opn-key"}, "api_secret": {"$ref": "opn-secret"}}})
        d = r.get_json()
        assert d["secret_refs"] == {"api_key": "opn-key", "api_secret": "opn-secret"} and d["settings"]["api_key"] == ""
        assert run(admin, "fw", "test")["status"] == "succeeded"
        # rotate the global secret: every user of it follows
        admin.put("/api/secrets/opn-secret", json={"value": "wrong"})
        assert run(admin, "fw", "test")["status"] == "failed"
        assert admin.delete("/api/secrets/opn-secret").get_json()["error"].startswith("still used by fw")
    finally:
        opn.srv.close()
    ia = make_user("ia", role="integration-admin")
    assert ia.get("/api/secrets").status_code == 200
    assert ia.put("/api/secrets/x", json={"value": "y"}).status_code == 403
    assert make_user("v", role="viewer").get("/api/secrets").status_code == 403
    assert "opn-key" not in admin.get("/api/admin/backup").get_data(as_text=True)


# ------------------------------------------------------------- host modules
@pytest.fixture
def dns():
    t = FakeTechnitium("lab.example.net")
    t.records.append({"name": "unown.lab.example.net", "type": "A", "ttl": 300, "rData": {"ipAddress": "10.10.100.99"}, "comments": ""})
    t.records.append({"name": "srv-vm-dns-01.srv.lab.example.net", "type": "A", "ttl": 300, "rData": {"ipAddress": "10.10.100.53"}, "comments": "labdex"})
    yield t
    t.srv.close()


def test_dns_selectors_custom_records_and_host_view(admin, make_user, dns):
    make_instance(admin, "technitium", "dns", {"url": dns.url, "token": "t0ken", "verify_tls": False,
                  "custom_records": [{"name": "@", "type": "MX", "value": "10 mail.example.org"},
                                     {"name": "nas", "type": "CNAME", "value": "srv-pm-pve-01.lab.example.net"},
                                     {"name": "", "type": "", "value": ""}]})
    r = admin.put("/api/integrations/dns", json={"selectors": "zone:DC_TC bogus"})
    assert r.status_code == 422
    admin.put("/api/integrations/dns", json={"selectors": ["zone:DC_TC"]})
    assert admin.get("/api/integrations/dns").get_json()["hosts"][:2] == ["srv-vm-dns-01", "srv-vm-ha-01"]

    mods = admin.get("/api/hosts/srv-vm-dns-01/modules").get_json()
    m = next(x for x in mods if x["integration"] == "dns")
    assert m["enabled"] and m["why"] == "selector" and m["host_settings"][0]["key"] == "skip_generated"
    assert not next(x for x in admin.get("/api/hosts/sonos-living/modules").get_json() if x["integration"] == "dns")["enabled"]

    # per-host records live on the host (0.5.2) and reach Technitium like generated ones
    r = admin.put("/api/hosts/srv-vm-dns-01", json={**host(admin, "srv-vm-dns-01"), "records": [{"type": "A", "value": "x"}]})
    assert r.status_code == 422 and "records[0].value" in str(r.get_json())
    put_host(admin, "srv-vm-dns-01", records=[{"name": "resolver", "type": "A", "value": "{ip4}"},
                                              {"name": "", "type": "TXT", "value": "owner=mike"}])
    admin.put("/api/hosts/srv-vm-mon-01/modules/dns", json={"enabled": False})  # opt one host out

    plan = admin.get("/api/jobs?integration=dns&status=planned").get_json()[0]
    names = {c["name"] for c in admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]}
    assert "resolver.lab.example.net A" in names and "srv-vm-dns-01.lab.example.net TXT" in names
    assert "lab.example.net MX" in names and "nas.lab.example.net CNAME" in names
    assert not any("alakazam" in n or "srv-vm-mon-01" in n for n in names)  # opted out
    assert not any("sonos" in n or "arcanine" in n for n in names)          # not selected
    admin.post(f"/api/jobs/{plan['id']}/apply")
    assert any(r["type"] == "MX" for r in dns.records)

    view = admin.get("/api/hosts/srv-vm-dns-01/modules/dns/view").get_json()
    rows = {(r["name"], r["type"]): r for r in view["sections"][0]["rows"]}
    assert rows[("unown.lab.example.net", "A")]["_tone"] == "err"  # unmanaged, wrong address
    assert rows[("resolver.lab.example.net", "A")]["status"] == "in sync"
    assert rows[("unown.lab.example.net", "CNAME")]["status"] == "in sync"

    v = make_user("v", role="viewer")
    assert v.put("/api/hosts/srv-vm-dns-01/modules/dns", json={"enabled": False}).status_code == 403
    assert v.get("/api/hosts/srv-vm-dns-01/modules").status_code == 200
    hist = admin.get("/api/history?object_type=hostmodule").get_json()
    assert hist[0]["object_name"] == "srv-vm-mon-01/dns"
    # renames follow
    put_host(admin, "srv-vm-mon-01", name="srv-vm-mon-02")
    m = next(x for x in admin.get("/api/hosts/srv-vm-mon-02/modules").get_json() if x["integration"] == "dns")
    assert m["override"] is False


@pytest.fixture
def fw():
    f = FakeOPNsense()
    f.add("Admin_Hosts", "10.10.10.5", "hand-made")
    f.add("Backup_Targets", "", "hand-made")
    f.rules = [
        {"uuid": "r1", "enabled": "1", "action": "pass", "interface": "lan", "protocol": "TCP", "source_net": "N4_ZONE_CA_ST_MAIN",
         "destination_net": "H_ZONE_DC_TC_SRV_VM_DNS_01", "destination_port": "5380", "description": "admin UI from main"},
        {"uuid": "r2", "enabled": "1", "action": "block", "interface": "iot", "protocol": "any", "source_net": "10.10.201.0/24",
         "destination_net": "10.10.100.0/24", "destination_port": "", "description": "iot never reaches servers"},
    ]
    yield f
    f.srv.close()


def test_firewall_membership_rules_and_access(admin, fw):
    make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False,
                  "extend_aliases": [{"alias": "Backup_Targets", "member": "srv-pm-pve-01"}, {"alias": "Missing", "member": "1.2.3.4"}]})
    admin.put("/api/hosts/srv-vm-dns-01/modules/fw", json={"settings": {"member_of": ["Admin_Hosts"]}})
    plan = run(admin, "fw", "sync_aliases")
    members = {c["name"]: c for c in plan["plan"] if c["kind"] == "alias members"}
    assert members["Admin_Hosts"]["summary"] == "add H_ZONE_DC_TC_SRV_VM_DNS_01"
    assert members["Backup_Targets"]["summary"] == "add H_ZONE_DC_HT_SRV_PM_PVE_01"
    assert admin.post(f"/api/jobs/{plan['id']}/apply").get_json()["status"] == "applied"
    assert fw.by_name("Admin_Hosts")["content"] == "10.10.10.5\nH_ZONE_DC_TC_SRV_VM_DNS_01"

    # someone adds a member by hand; labdex later removes only what it added
    a = fw.by_name("Admin_Hosts")
    a["content"] += "\n10.10.10.6"
    admin.put("/api/hosts/srv-vm-dns-01/modules/fw", json={"settings": {"member_of": []}})
    plan = admin.get("/api/jobs?integration=fw&status=planned").get_json()[0]
    detail = admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]
    assert [c["summary"] for c in detail if c["kind"] == "alias members"] == ["remove H_ZONE_DC_TC_SRV_VM_DNS_01"]
    admin.post(f"/api/jobs/{plan['id']}/apply")
    assert fw.by_name("Admin_Hosts")["content"] == "10.10.10.5\n10.10.10.6"

    # declared access: main may reach dns-admin (rule r1, IPv4); CA_UT may reach dns (no rule: r2 blocks)
    h = host(admin, "srv-vm-dns-01")
    for s in h["services"]:
        s["allow_from"] = {"dns-admin": ["main"], "dns": ["CA_UT", "nonsense"]}[s["name"]]
    put_host(admin, "srv-vm-dns-01", services=h["services"])
    view = admin.get("/api/hosts/srv-vm-dns-01/modules/fw/view").get_json()
    access = {(r["service"], r["from"]): r for r in view["sections"][0]["rows"]}
    # the rule uses the IPv4 alias only, and the host also has IPv6
    assert access[("dns-admin", "main")]["status"] == "IPv4 allowed, IPv6: no pass rule"
    assert access[("dns-admin", "main")]["rule"] == "admin UI from main"
    assert access[("dns", "CA_UT")]["status"] == "no pass rule found"
    assert access[("dns", "nonsense")]["_tone"] == "warn"
    rules = view["sections"][1]["rows"]
    assert [r["description"] for r in rules] == ["admin UI from main", "iot never reaches servers"]
    assert rules[1]["_tone"] == "err" and rules[0]["direction"] == "to host"
    job = run(admin, "fw", "check_access")
    assert job["result"]["missing"] == 2


# -------------------------------------------------------------- Ansible runner
@pytest.mark.skipif(not shutil.which("ansible-playbook"), reason="ansible not installed")
def test_ansible_runner_features(admin, tmp_path):
    src = tmp_path / "ansible"
    (src / "roles" / "noop" / "tasks").mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()
    (src / "write.yml").write_text(textwrap.dedent(f"""
        - hosts: all
          gather_facts: false
          tasks:
            - ansible.builtin.copy:
                dest: "{out}/{{{{ inventory_hostname }}}}.txt"
                content: "{{{{ greeting }}}} {{{{ nested.a }}}}{{{{ nested.b }}}} {{{{ pkgs | join(',') }}}}\\n"
    """))
    (src / "roles" / "noop" / "tasks" / "main.yml").write_text(f"- ansible.builtin.copy: {{dest: '{out}/{{{{ inventory_hostname }}}}.role', content: '{{{{ version }}}}'}}\n")
    make_tree(admin)
    put_host(admin, "srv-vm-k3s-01", features=["server.packages.pve", "kube.cluster.oak"], vars={"greeting": "moin"})
    put_host(admin, "srv-vm-k3s-02", features=["kube"])
    make_instance(admin, "ansible", "runner", {"source": "folder", "path": str(src), "connection": "local", "become": False,
                                                "host_key_checking": False})
    assert run(admin, "runner", "test")["status"] == "succeeded"

    plan = run(admin, "runner", "apply_features")
    detail = admin.get(f"/api/jobs/{plan['id']}").get_json()
    assert plan["status"] == "planned", detail["log"]
    assert [c["name"] for c in detail["plan"]] == ["server.packages.pve: write.yml", "kube: noop"]
    assert detail["plan"][1]["after"]["hosts"] == ["srv-vm-k3s-01", "srv-vm-k3s-02"]
    assert not list(out.iterdir())  # check mode
    done = admin.post(f"/api/jobs/{plan['id']}/apply").get_json()
    assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert (out / "srv-vm-k3s-01.txt").read_text() == "moin 12 vim,htop\n"
    assert (out / "srv-vm-k3s-01.role").read_text() == "1.31" and (out / "srv-vm-k3s-02.role").read_text() == "1.30"
    assert run(admin, "runner", "apply_features")["status"] == "succeeded"  # idempotent

    # only one branch of the tree
    p = admin.post("/api/integrations/runner/actions/apply_features", json={"params": {"feature": "kube"}}).get_json()
    assert p["status"] == "succeeded" and p["params"]["feature"] == "kube"

    # per host: features and an ad-hoc command
    (out / "srv-vm-k3s-01.txt").unlink()
    r = admin.post("/api/hosts/srv-vm-k3s-01/modules/runner/actions/host_features")
    assert r.status_code == 202 and r.get_json()["status"] == "planned"
    r = admin.post("/api/hosts/srv-vm-k3s-01/modules/runner/actions/command", json={"params": {"command": "echo hello-from-$((40+2))"}})
    job = admin.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert job["status"] == "succeeded" and "hello-from-42" in job["log"]
    r = admin.post("/api/hosts/srv-vm-k3s-01/modules/runner/actions/command", json={"params": {}})
    assert r.status_code == 409 and "command: required" in r.get_json()["error"]


def test_host_actions_need_host_apply(admin, make_user):
    make_instance(admin, "ansible", "runner", {"source": "folder", "path": "/nonexistent"})
    op = make_user("op", role="operator", scopes=["zone:CA_*"])
    assert op.post("/api/hosts/srv-vm-k3s-01/modules/runner/actions/command", json={"params": {"command": "id"}}).status_code == 404
    ed = make_user("ed", role="editor")
    assert ed.post("/api/hosts/srv-vm-k3s-01/modules/runner/actions/command", json={"params": {"command": "id"}}).status_code == 403


# ------------------------------------------------------------------ backups
def test_backup_folder_encrypted_retention_and_restore(admin, tmp_path, loaded):
    target = tmp_path / "bk"
    make_instance(admin, "backup", "bk", {"path": str(target), "passphrase": "correct horse", "keep": 2})
    for _ in range(3):
        job = run(admin, "bk", "backup")
        assert job["status"] == "succeeded", job
        import time
        time.sleep(1.1)  # file names have second resolution
    files = sorted(target.iterdir())
    assert len(files) == 2 and all(f.name.endswith(".tar.gz.enc") for f in files)
    blob = files[-1].read_bytes()
    with pytest.raises(backup.BackupError):
        backup.open_archive(blob, "wrong")
    content = backup.open_archive(blob, "correct horse")
    assert {"database.json", "inventory.yaml", "manifest.json"} <= set(content)
    assert ("labdex.db" in content) == (db.engine.url.get_backend_name() == "sqlite")
    assert b"srv-vm-dns-01" in content["inventory.yaml"]
    assert b"correct horse" not in content["database.json"]  # the passphrase is stored encrypted
    assert len(admin.get("/api/integrations/bk/endpoints/backups").get_json()) == 2

    r = admin.get("/api/integrations/bk/export/download")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and backup.is_encrypted(r.data)

    # CLI: restore the inventory from the backup
    admin.delete("/api/hosts/sonos-living")
    runner = loaded.test_cli_runner()
    res = runner.invoke(args=["backup", "restore", str(files[-1]), "--inventory-only", "--yes", "--passphrase", "correct horse"])
    assert res.exit_code == 0, res.output
    assert admin.get("/api/hosts/sonos-living").status_code == 200
    res = runner.invoke(args=["backup", "check", str(files[-1]), "--passphrase", "nope"])
    assert res.exit_code != 0 and "wrong passphrase" in res.output


def test_backup_to_s3(admin):
    s3 = FakeS3()
    try:
        make_instance(admin, "backup", "offsite", {"target": "s3", "s3_endpoint": s3.url, "s3_bucket": "bk", "s3_access_key": "AK",
                                                   "s3_secret_key": "SK", "s3_prefix": "lab/", "keep": 1})
        job = run(admin, "offsite", "backup")
        assert job["status"] == "failed" and "passphrase" in job["result"]["error"]
        admin.put("/api/integrations/offsite", json={"settings": {"passphrase": "pw"}})
        assert run(admin, "offsite", "backup")["status"] == "succeeded"
        import time
        time.sleep(1.1)
        assert run(admin, "offsite", "backup")["status"] == "succeeded"
        assert len(s3.objects) == 1 and next(iter(s3.objects)).startswith("lab/labdex-backup-")
        assert backup.open_archive(next(iter(s3.objects.values())), "pw")["manifest.json"]
        assert "/aws4_request" in s3.auth[0]
    finally:
        s3.srv.close()


def test_backup_module_exists_by_default(tmp_path):
    from labdex import create_app
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'd.db'}", "TESTING": True, "SECRET_KEY": "s",
                      "LABDEX_PLUGIN_DIR": str(tmp_path / "p"), "LABDEX_SCHEDULER": False, "LABDEX_AUTO_BACKUP": True})
    with app.app_context():
        i = db.session.scalar(db.select(Integration).where(Integration.plugin_id == "backup"))
        assert i.enabled and i.settings["schedule_backup"] == 1440
    c = client(app)
    c.post("/api/auth/setup", json=ADMIN)
    assert c.get("/api/integrations/backup").get_json()["plugin"]["per_host"] is False
