"""v0.6.3: Ansible Vault, secrets in Ansible variables, job templates."""
import shutil
import subprocess
import textwrap
from datetime import datetime, timedelta

import pytest
import yaml

from labdex import ansiblevault, templates
from labdex.models import Job, JobTemplate, db, utcnow
from labdex.plugins import installer

from .test_plugins import make_instance, run, write_plugin
from .test_v03 import host, put_host

ANSIBLE = shutil.which("ansible-playbook")


def test_vault_roundtrip_and_refs():
    t = ansiblevault.encrypt("s3cr€t", "pw")
    assert t.startswith("$ANSIBLE_VAULT;1.1;AES256\n") and ansiblevault.decrypt(t, "pw") == "s3cr€t"
    assert ansiblevault.encrypt("x", "pw", "prod").startswith("$ANSIBLE_VAULT;1.2;AES256;prod\n")
    with pytest.raises(ansiblevault.VaultError):
        ansiblevault.decrypt(t, "wrong")
    obj = {"a": {"$secret": "one"}, "b": [1, {"c": {"$secret": "two"}}], "d": {"$secret": "x", "other": 1}}
    assert ansiblevault.refs(obj) == {"one", "two"}
    assert ansiblevault.replace(obj, str.upper)["b"][1]["c"] == "TWO"


@pytest.mark.skipif(not shutil.which("ansible-vault", path="/usr/local/bin:/usr/bin:/bin"), reason="ansible not installed where labdex runs it")
def test_vault_matches_ansible(tmp_path):
    (tmp_path / "pass").write_text("pw\n")
    (tmp_path / "v.txt").write_text(ansiblevault.encrypt("from labdex", "pw", "lab") + "\n")
    out = subprocess.run(["ansible-vault", "view", "--vault-id", f"lab@{tmp_path / 'pass'}", str(tmp_path / "v.txt")],
                         capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/local/bin:/usr/bin:/bin"})
    assert out.stdout.strip() == "from labdex", out.stderr


def test_secret_refs_in_vars_lint_usage_and_export(admin, vault):
    admin.post("/api/features", json={"path": "grafana", "vars": "admin_password: {$secret: grafana-admin}\nport: 3000"})
    put_host(admin, "srv-vm-dns-01", features=["grafana"], vars={"api_key": {"$secret": "nope"}})
    lint = {(i["code"], i["ref"]) for i in admin.get("/api/lint").get_json()["issues"]}
    assert ("secret-missing", "grafana") in lint and ("secret-missing", "srv-vm-dns-01") in lint
    admin.put("/api/secrets/grafana-admin", json={"value": "correct horse"})
    admin.put("/api/secrets/nope", json={"value": "k3y"})
    lint = {(i["code"], i["ref"]) for i in admin.get("/api/lint").get_json()["issues"]}
    assert not any(c == "secret-missing" for c, _ in lint)
    used = {s["name"]: s["used_by"] for s in admin.get("/api/secrets").get_json()}
    assert used["grafana-admin"] == ["feature grafana"] and used["nope"] == ["host srv-vm-dns-01"]

    # the export keeps references without a vault password ...
    make_instance(admin, "exports", "files", {})
    text = admin.get("/api/integrations/files/export/ansible_inventory").get_data(as_text=True)
    assert "correct horse" not in text and "$secret: grafana-admin" in text
    # ... and makes !vault values with one
    admin.put("/api/integrations/files", json={"settings": {"vault_password": "vpw", "vault_id": "lab"}})
    text = admin.get("/api/integrations/files/export/ansible_inventory").get_data(as_text=True)
    assert "correct horse" not in text and "!vault |" in text and "$ANSIBLE_VAULT;1.2;AES256;lab" in text

    class L(yaml.SafeLoader):
        pass
    L.add_constructor("!vault", lambda loader, node: ansiblevault.decrypt(loader.construct_scalar(node), "vpw"))
    hv = yaml.load(text, Loader=L)["all"]["hosts"]["srv-vm-dns-01"]
    assert hv["admin_password"] == "correct horse" and hv["api_key"] == "k3y" and hv["port"] == 3000
    # nothing in the YAML export or the host API
    assert "correct horse" not in admin.get("/api/export").get_data(as_text=True)
    assert host(admin, "srv-vm-dns-01")["vars"] == {"api_key": {"$secret": "nope"}}


@pytest.mark.skipif(not ANSIBLE, reason="ansible not installed")
def test_ansible_run_gets_secrets_and_vault_password(admin, tmp_path, vault):
    src, out = tmp_path / "ansible", tmp_path / "out"
    src.mkdir()
    out.mkdir()
    (src / "vaulted.yml").write_text(ansiblevault.encrypt("vaulted: from-vault\n", "vpw") + "\n")  # an ansible-vault file
    (src / "write.yml").write_text(textwrap.dedent(f"""
        - hosts: all
          gather_facts: false
          vars_files: [vaulted.yml]
          tasks:
            - ansible.builtin.copy:
                dest: "{out}/{{{{ inventory_hostname }}}}.txt"
                content: "{{{{ vaulted }}}} {{{{ db_password }}}}\\n"
    """))
    admin.put("/api/secrets/db", json={"value": "pa55"})
    admin.put("/api/secrets/vault", json={"value": "vpw"})
    admin.post("/api/features", json={"path": "app", "vars": {"db_password": {"$secret": "db"}}, "playbooks": ["write.yml"]})
    put_host(admin, "srv-vm-k3s-01", features=["app"])
    make_instance(admin, "ansible", "runner", {"source": "folder", "path": str(src), "connection": "local", "become": False,
                                                "host_key_checking": False, "vault_password": {"$ref": "vault"}})
    plan = run(admin, "runner", "apply_features")
    log = admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert plan["status"] == "planned", log
    assert "pa55" not in log and "vpw" not in log and "--vault-password-file" in log and "from-vault ********" in log
    done = admin.post(f"/api/jobs/{plan['id']}/apply").get_json()
    assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert (out / "srv-vm-k3s-01.txt").read_text() == "from-vault pa55\n"


# ------------------------------------------------------------------ templates
DEMO = """
id: demo
name: Demo
actions:
  - {id: greet, mode: run, params: [{key: who, type: string, required: true}]}
  - {id: poke, mode: run, scope: host}
"""
CODE = """
from labdex.plugins.base import Plugin as Base
class Plugin(Base):
    def run_greet(self, params):
        return {"hello": params["who"]}
    def run_poke(self, params):
        return {"poked": params["host"]}
"""


@pytest.fixture
def demo(loaded, admin):
    write_plugin(loaded, "demo", DEMO, CODE)
    installer.reload_registry(loaded)
    admin.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
    admin.put("/api/integrations/demo1", json={"enabled": True})
    return admin


def test_templates_run_and_validate(demo, make_user):
    def errs(data):
        r = demo.post("/api/job-templates", json=data)
        assert r.status_code == 422, r.get_json()
        return {e["field"]: e["message"] for e in r.get_json()["errors"]}
    assert "params.who" in errs({"name": "hi", "integration": "demo1", "action": "greet"})
    assert "host" in errs({"name": "p", "integration": "demo1", "action": "poke"})
    assert "every_minutes" in errs({"name": "p", "integration": "demo1", "action": "greet", "params": {"who": "x"}, "schedule": "every", "every_minutes": 1})
    assert "action" in errs({"name": "p", "integration": "demo1", "action": "nope"})

    r = demo.post("/api/job-templates", json={"name": "Hello world", "integration": "demo1", "action": "greet", "params": {"who": "world"}})
    assert r.status_code == 201, r.get_json()
    t = r.get_json()
    assert t["next_run_at"] is None and t["action_label"] == "Greet"
    job = demo.post(f"/api/job-templates/{t['id']}/run").get_json()
    assert job["status"] == "succeeded" and job["result"] == {"hello": "world"} and job["template"] == "Hello world"
    poke = demo.post("/api/job-templates", json={"name": "poke dns", "integration": "demo1", "action": "poke", "host": "srv-vm-dns-01"}).get_json()
    job = demo.post(f"/api/job-templates/{poke['id']}/run").get_json()
    assert job["result"] == {"poked": "srv-vm-dns-01"} and job["hosts"] == ["srv-vm-dns-01"]
    assert [x["name"] for x in demo.get("/api/job-templates?integration=demo1").get_json()] == ["Hello world", "poke dns"]
    # follows a host rename
    h = host(demo, "srv-vm-dns-01")
    demo.put("/api/hosts/srv-vm-dns-01", json={**h, "name": "srv-vm-dns-07"})
    assert demo.get("/api/job-templates").get_json()[1]["host"] == "srv-vm-dns-07"

    # operators run templates; a schedule (runs unattended) needs integration:admin
    op = make_user("op", role="operator")
    assert op.post(f"/api/job-templates/{t['id']}/run").status_code == 202
    assert op.put(f"/api/job-templates/{t['id']}", json={"schedule": "every", "every_minutes": 60}).status_code == 403
    assert op.post("/api/job-templates", json={"name": "mine", "integration": "demo1", "action": "greet", "params": {"who": "me"}}).status_code == 201


def test_scheduled_templates(demo, monkeypatch):
    r = demo.post("/api/job-templates", json={"name": "hourly", "integration": "demo1", "action": "greet", "params": {"who": "h"},
                                              "schedule": "every", "every_minutes": 60})
    t = r.get_json()
    nxt = datetime.fromisoformat(t["next_run_at"][:-1])
    assert timedelta(minutes=59) < nxt - utcnow() <= timedelta(minutes=60)
    assert templates.due() == []
    row = db.session.get(JobTemplate, t["id"])
    row.next_run_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    started = templates.due()
    assert len(started) == 1
    job = db.session.get(Job, started[0])
    assert job.trigger == "schedule" and job.created_by == "template hourly" and job.status == "succeeded"
    assert row.next_run_at - row.last_run_at == timedelta(minutes=60)

    w = demo.post("/api/job-templates", json={"name": "weekly", "integration": "demo1", "action": "greet", "params": {"who": "w"},
                                              "schedule": "weekly", "days": ["mon"], "at": "03:30", "timezone": "UTC"}).get_json()
    nxt = datetime.fromisoformat(w["next_run_at"][:-1])
    assert nxt.weekday() == 0 and (nxt.hour, nxt.minute) == (3, 30) and nxt > utcnow()
    assert demo.put(f"/api/job-templates/{w['id']}", json={"enabled": False}).get_json()["next_run_at"] is None
