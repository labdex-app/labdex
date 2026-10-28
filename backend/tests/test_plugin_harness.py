"""labdex.testing: the harness and the fake server that plugin authors test with."""
import pytest
import requests

from labdex.plugins.base import Change
from labdex.testing import FakeServer, PluginHarness

MANIFEST = """\
id: demo-dns
name: Demo DNS
kind: integration
version: "0.1"
labdex: ">=1.0,<2"
settings:
  - {key: url, label: URL, type: string, required: true}
host_settings:
  - {key: records, type: list}
actions:
  - {id: test, label: Test, mode: run}
  - {id: sync, label: Sync, mode: plan}
  - {id: files, label: Files, mode: export}
widgets:
  - {id: count, label: Hosts, where: dashboard}
host_view: true
"""

CODE = '''
from labdex.plugins.base import Change, ExportFile, Plugin as Base, PluginError


class Plugin(Base):
    def run_test(self, params):
        info = self.ctx.http(self.settings["url"]).get("info").json()
        self.ctx.set_state("version", info["version"])
        return {"message": f"ok {info['version']}"}

    def plan_sync(self, params):
        have = {r["name"] for r in self.ctx.http(self.settings["url"]).get("records").json()}
        return [Change("create", "record", h["name"], "A") for h in self.ctx.inventory.hosts() if h["name"] not in have]

    def apply_sync(self, changes, params):
        http = self.ctx.http(self.settings["url"])
        for c in changes:
            http.post("records", json={"name": c.name})
        self.ctx.notify("synced", "Records synced", f"{len(changes)} added")
        return {"applied": len(changes)}

    def export_files(self, params):
        return ExportFile("names.txt", "text/plain", "\\n".join(h["name"] for h in self.ctx.inventory.hosts()))

    def widget_count(self, host):
        return {"kind": "stat", "value": len(self.ctx.inventory.hosts()), "unit": "hosts"}

    def host_view(self, host):
        return {"summary": [{"label": "extra", "value": len(host["module_settings"].get("records", []))}]}
'''

INVENTORY = {
    "zones": [{"code": "DC_TC", "name": "Datacenter", "rank": 4}],
    "areas": [{"code": "DC", "name": "Datacenter"}],
    "networks": [{"name": "srv", "zone": "DC_TC", "prefix4": "10.0.0.0/24"}],
    "hosts": [{"name": "srv-vm-a-01", "zone": "DC_TC", "interfaces": [{"network": "srv", "ip4": "10.0.0.5"}]},
              {"name": "srv-vm-b-01", "zone": "DC_TC", "interfaces": [{"network": "srv", "ip4": "10.0.0.6"}]}],
}


@pytest.fixture
def plugin_dir(tmp_path):
    d = tmp_path / "demo-dns"
    d.mkdir()
    (d / "plugin.yaml").write_text(MANIFEST)
    (d / "plugin.py").write_text(CODE)
    return d


def test_fake_server_routes_and_records():
    with FakeServer() as api:
        api.route("GET", "/a", json={"x": 1}).route("POST", "/b", status=201, text="made").route("GET", "/deep/*", json=[1])
        api.route("PUT", "/c", handler=lambda call: (202, {"got": call.json}))
        assert requests.get(f"{api.url}/a?q=1&q=2&r=3").json() == {"x": 1}
        assert requests.post(f"{api.url}/b", json={"k": "v"}).status_code == 201
        assert requests.get(f"{api.url}/deep/er/still").json() == [1]
        r = requests.put(f"{api.url}/c", json={"n": 5})
        assert r.status_code == 202 and r.json() == {"got": {"n": 5}}
        assert requests.get(f"{api.url}/nope").status_code == 404
        assert api.calls("GET", "/a")[0].query == {"q": ["1", "2"], "r": "3"}
        assert api.calls("POST")[0].json == {"k": "v"} and len(api.requests) == 5


def test_run_plan_apply_export_widget_state(plugin_dir):
    with FakeServer() as api, PluginHarness(plugin_dir, settings={"url": api.url}, inventory=INVENTORY) as h:
        api.route("GET", "/info", json={"version": "9"}).route("GET", "/records", json=[{"name": "srv-vm-a-01"}]).route("POST", "/records", status=201)
        assert h.run("test") == {"message": "ok 9"} and h.state == {"version": "9"}
        changes = h.plan("sync")
        assert [(c.op, c.name) for c in changes] == [("create", "srv-vm-b-01")] and isinstance(changes[0], Change)
        assert h.apply("sync", changes) == {"applied": 1}
        assert api.calls("POST", "/records")[0].json == {"name": "srv-vm-b-01"}
        assert h.notifications[0]["event"] == "synced" and any("POST records" in line for line in h.logs)
        assert h.export("files").body == "srv-vm-a-01\nsrv-vm-b-01"
        assert h.widget("count") == {"kind": "stat", "value": 2, "unit": "hosts"}


def test_host_view_with_host_settings(plugin_dir):
    with PluginHarness(plugin_dir, settings={}, inventory=INVENTORY, host_settings={"srv-vm-a-01": {"records": ["x", "y"]}}) as h:
        assert h.host_view("srv-vm-a-01")["summary"][0]["value"] == 2
        assert h.host_view("srv-vm-b-01")["summary"][0]["value"] == 0
        with pytest.raises(AssertionError, match="no host"):
            h.host_view("nope")


def test_errors_are_readable(plugin_dir, tmp_path):
    with PluginHarness(plugin_dir, settings={}) as h:          # no inventory: the example zones, no hosts
        assert h.ctx.hosts() == []
        with pytest.raises(AssertionError, match="no run_missing"):
            h.run("missing")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "plugin.yaml").write_text("id: Bad Id\n")
    with pytest.raises(AssertionError, match="id: lowercase"):
        PluginHarness(bad).__enter__()
    with pytest.raises(FileNotFoundError):
        PluginHarness(tmp_path / "nothing").__enter__()


def test_inventory_from_yaml_text_and_file(plugin_dir, tmp_path):
    import yaml
    text = yaml.safe_dump(INVENTORY)
    f = tmp_path / "inv.yaml"
    f.write_text(text)
    for source in (text, f, str(f)):
        with PluginHarness(plugin_dir, inventory=source) as h:
            assert len(h.ctx.hosts()) == 2
