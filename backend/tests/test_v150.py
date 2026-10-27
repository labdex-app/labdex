"""1.5: community and unsigned plugins run isolated; they can use the plugin API but not
labdex's database, secrets, memory or other plugins."""
import json

import pytest

from labdex.models import Integration, db
from labdex.plugins import sandbox

from .test_plugins import make_instance, run
from .test_v04 import make_plugin, upload, zip_dir

needs_landlock = pytest.mark.skipif(not sandbox.abi(), reason="this kernel has no Landlock")

MANIFEST = """
    id: snoop
    name: Snoop
    version: "1.0"
    kind: integration
    settings:
      - {key: token, label: Token, type: secret}
      - {key: target, label: Path to try, type: string}
    host_settings:
      - {key: note, label: Note, type: string}
    pages:
      - {id: look, label: Look}
    widgets:
      - {id: count, label: Count, where: dashboard}
    actions:
      - {id: probe, mode: run}
      - {id: tidy, mode: plan}
      - {id: dump, mode: export}
      - {id: crash, mode: run}
      - {id: forbidden, mode: run}
"""

CODE = """
    import os
    from labdex.plugins.base import Change, ExportFile, Plugin as Base, PluginError

    def tryread(path):
        try:
            with open(path, "rb") as f:
                return "READ " + str(len(f.read()))
        except OSError as exc:
            return "blocked " + type(exc).__name__

    class Helper(Base):
        def helper_method(self):
            return "inherited"

    class Plugin(Helper):
        def run_probe(self, params):
            print("hello from print")
            self.log("hello from log")
            out = {
                "target": tryread(self.settings["target"]),
                "own_token": self.settings.get("token"),
                "env": sorted(k for k in os.environ if k.startswith(("LABDEX", "SECRET", "DATABASE"))),
                "parent_environ": tryread(f"/proc/{os.getppid()}/environ"),
                "parent_mem": tryread(f"/proc/{os.getppid()}/mem"),
                "hosts": len(self.ctx.inventory.hosts()),
                "networks": [n["name"] for n in self.ctx.inventory.networks()][:2],
                "match": self.ctx.match_host()("10.10.100.53"),
                "mine": [h["name"] for h in self.ctx.hosts()][:1],
                "inherited": self.helper_method(),
            }
            self.ctx.set_state("seen", out["hosts"])
            (self.ctx.data_dir / "cache.txt").write_text("kept")
            out["state"] = self.ctx.state.get("seen")
            out["data_dir_call"] = str(self.ctx.data_dir()) == str(self.ctx.data_dir)
            try:
                from labdex import secretstore
                out["secretstore"] = secretstore.reveal("other")
            except Exception as exc:
                out["secretstore"] = "failed " + type(exc).__name__
            return {"message": json.dumps(out)} if False else {"message": "ok", "out": out}

        def plan_tidy(self, params):
            return [Change("update", "thing", "a", "make a tidy", before={"x": 1}, after={"x": 2}, data={"n": 7})]

        def apply_tidy(self, changes, params):
            c = changes[0]
            return {"applied": c.data["n"], "kind": type(c).__name__}

        def export_dump(self, params):
            return ExportFile("dump.bin", "application/octet-stream", bytes(range(5)))

        def run_crash(self, params):
            raise ZeroDivisionError("nope")

        def run_forbidden(self, params):
            return {"message": self.ctx._wire.call("secrets.reveal", "x")}

        def page_look(self, args):
            raise PluginError("nothing to see")

        def widget_count(self, host):
            return {"kind": "stat", "value": 42}
"""


@pytest.fixture
def snoop(admin, tmp_path, app):
    data = tmp_path / "data"
    data.mkdir()
    app.config["LABDEX_DATA_DIR"] = str(data)
    (data / "secret.key").write_text("TOP-SECRET")
    d = make_plugin(tmp_path, files={"plugin.yaml": MANIFEST, "plugin.py": CODE.replace("json.dumps(out)", "out")})
    info = upload(admin, zip_dir(d)).get_json()
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm", json={"accept_unsigned": True}).status_code == 201
    # another integration with a secret the plugin must never see
    make_instance(admin, "uptimekuma", "kuma", {"url": "http://127.0.0.1:9", "username": "u", "password": "kuma-password"})
    make_instance(admin, "snoop", "snoop", {"token": "own-token", "target": str(data / "secret.key")})
    return data


def test_isolated_flag(admin, snoop):
    p = admin.get("/api/plugins/snoop").get_json()
    assert p["isolated"] and p["trust"]["status"] == "unsigned"
    assert not admin.get("/api/plugins/opnsense").get_json()["isolated"]  # official: inside labdex
    st = admin.get("/api/plugins").get_json()["isolation"]
    assert st["mode"] == "on" and st["level"] in ("full", "partial")


def test_plugin_api_works_isolated(admin, snoop):
    job = run(admin, "snoop", "probe")
    assert job["status"] == "succeeded", job
    out = job["result"]["out"]
    assert out["own_token"] == "own-token"  # its own secret: yes
    assert out["env"] == []  # no labdex environment
    assert out["hosts"] > 5 and out["networks"] and out["mine"]
    assert out["match"] == "srv-vm-dns-01" and out["inherited"] == "inherited"
    assert out["state"] == out["hosts"] and out["data_dir_call"]
    assert out["secretstore"].startswith("failed")  # labdex's internals aren't reachable from there
    log = admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    assert "hello from print" in log and "hello from log" in log
    integ = db.session.scalar(db.select(Integration).where(Integration.name == "snoop"))
    db.session.refresh(integ)
    assert integ.state["plugin"]["seen"] == out["hosts"]
    assert (snoop / "plugin-data" / "snoop" / "cache.txt").read_text() == "kept"


@needs_landlock
def test_locked_out_of_labdex(admin, snoop):
    out = run(admin, "snoop", "probe")["result"]["out"]
    assert out["target"].startswith("blocked")  # secret.key in labdex's data folder
    assert out["parent_environ"].startswith("blocked") and out["parent_mem"].startswith("blocked")
    # the database file and another plugin's folder, too
    for target in [db.engine.url.database, str(snoop.parent / "plugins")]:
        admin.put("/api/integrations/snoop", json={"settings": {"target": target}})
        assert run(admin, "snoop", "probe")["result"]["out"]["target"].startswith("blocked"), target


def test_plans_exports_and_failures(admin, snoop):
    plan = run(admin, "snoop", "tidy")
    d = admin.get(f"/api/jobs/{plan['id']}").get_json()
    assert d["plan"][0]["summary"] == "make a tidy" and d["plan"][0]["after"] == {"x": 2}
    done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied" and done["result"] == {"applied": 7, "kind": "Change"}
    r = admin.get("/api/integrations/snoop/export/dump")
    assert r.status_code == 200 and r.data == bytes(range(5))
    crash = run(admin, "snoop", "crash")
    assert crash["status"] == "failed" and crash["result"]["error"] == "ZeroDivisionError: nope"
    assert "ZeroDivisionError" in admin.get(f"/api/jobs/{crash['id']}").get_json()["log"]
    bad = run(admin, "snoop", "forbidden")
    assert bad["status"] == "failed" and "not part of the plugin API: secrets.reveal" in bad["result"]["error"]
    r = admin.get("/api/integrations/snoop/pages/look")
    assert r.status_code in (409, 422, 502) and "nothing to see" in json.dumps(r.get_json())
    assert '"value": 42' in json.dumps(admin.get("/api/widgets").get_json())


def test_off_runs_inside(admin, snoop, app):
    from labdex.plugins import installer
    app.config["LABDEX_PLUGIN_SANDBOX"] = "off"
    installer.reload_registry(app)
    assert not admin.get("/api/plugins/snoop").get_json()["isolated"]
    out = run(admin, "snoop", "probe")["result"]["out"]
    assert out["target"].startswith("READ")  # which is why it's on by default


def test_strict_without_landlock(admin, snoop, app, monkeypatch):
    monkeypatch.setattr(sandbox, "_abi", 0)
    app.config["LABDEX_PLUGIN_SANDBOX"] = "strict"
    job = run(admin, "snoop", "probe")
    assert job["status"] == "failed" and "need Landlock" in job["result"]["error"]
    app.config["LABDEX_PLUGIN_SANDBOX"] = "on"  # without Landlock, "on" still runs it in its own process
    out = run(admin, "snoop", "probe")["result"]["out"]
    assert out["env"] == [] and out["own_token"] == "own-token"


def test_method_names_from_source(tmp_path):
    f = tmp_path / "p.py"
    f.write_text("class A:\n    def a(self): pass\nclass Plugin(A):\n    def run_x(self, p): pass\n    def _private(self): pass\n")
    assert sandbox.method_names(f) == {"a", "run_x"}
    f.write_text("def broken(:\n")
    with pytest.raises(SyntaxError):
        sandbox.method_names(f)


ANSIBLE_MANIFEST = """
    id: playbooky
    name: Playbooky
    version: "1.0"
    kind: integration
    settings:
      - {key: connection, label: Connection, type: string, default: local}
      - {key: target, label: Path to try, type: string}
    actions:
      - {id: read, mode: run, ansible: playbooks/read.yml}
"""
PLAYBOOK = """
    - hosts: localhost
      gather_facts: false
      tasks:
        - name: try a file
          ansible.builtin.command: "cat {{ labdex_settings.target }}"
          register: out
          ignore_errors: true
        - name: say
          ansible.builtin.debug:
            msg: "RESULT={{ 'READ' if out.rc == 0 else 'blocked' }}"
"""


# the isolated plugin finds programs only on the sandbox's PATH (the image has ansible there)
@pytest.mark.skipif(not __import__("shutil").which("ansible-playbook", path="/usr/local/bin:/usr/bin:/bin"), reason="no ansible-playbook on the sandbox's PATH")
@needs_landlock
def test_isolated_playbooks_run_isolated(admin, tmp_path, app):
    data = tmp_path / "data"
    data.mkdir()
    app.config["LABDEX_DATA_DIR"] = str(data)
    (data / "secret.key").write_text("TOP-SECRET")
    d = tmp_path / "src-pb" / "playbooky"
    (d / "playbooks").mkdir(parents=True)
    import textwrap
    (d / "plugin.yaml").write_text(textwrap.dedent(ANSIBLE_MANIFEST))
    (d / "playbooks" / "read.yml").write_text(textwrap.dedent(PLAYBOOK))
    info = upload(admin, zip_dir(d)).get_json()
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm", json={"accept_unsigned": True}).status_code == 201
    assert admin.get("/api/plugins/playbooky").get_json()["isolated"]
    make_instance(admin, "playbooky", "pb", {"connection": "local", "target": str(data / "secret.key")})
    job = run(admin, "pb", "read")
    log = admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    assert "RESULT=blocked" in log and "TOP-SECRET" not in log, log[-3000:]
    # control: its own files it can read, so "blocked" above is the sandbox and not a broken run
    admin.put("/api/integrations/pb", json={"settings": {"connection": "local", "target": str(tmp_path / "plugins" / "playbooky" / "plugin.yaml")}})
    job = run(admin, "pb", "read")
    assert "RESULT=READ" in admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
