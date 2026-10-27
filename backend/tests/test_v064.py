"""v0.6.4: the dashboard."""
import pytest

from labdex.plugins import installer

from .test_plugins import write_plugin

DEMO = """
id: demo
name: Demo
actions:
  - {id: change, mode: plan}
  - {id: flaky, mode: run, params: [{key: fail, type: bool}]}
"""
CODE = """
from labdex.plugins.base import Change, Plugin as Base, PluginError
class Plugin(Base):
    def plan_change(self, params):
        return [Change("update", "thing", "x", "would change x")]
    def apply_change(self, changes, params):
        return {}
    def run_flaky(self, params):
        if params.get("fail"):
            raise PluginError("it broke")
        return {}
"""


@pytest.fixture
def demo(loaded, admin):
    write_plugin(loaded, "demo", DEMO, CODE)
    installer.reload_registry(loaded)
    admin.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
    admin.put("/api/integrations/demo1", json={"enabled": True})
    return admin


def test_dashboard(demo, make_user):
    d = demo.get("/api/dashboard").get_json()
    assert d["counts"]["hosts"] == 15 and d["counts"]["networks"] > 0 and "lint" in d["counts"]
    assert d["waiting"] == [] and d["failing"] == [] and d["health"]["jobs"]["ok"]
    assert all(e["action"] != "login" for e in d["changes"])

    plan = demo.post("/api/integrations/demo1/actions/change").get_json()
    demo.post("/api/integrations/demo1/actions/flaky", json={"params": {"fail": True}})
    d = demo.get("/api/dashboard").get_json()
    assert [j["id"] for j in d["waiting"]] == [plan["id"]] and d["waiting"][0]["can_approve"]
    assert d["failing"][0]["result"]["error"] == "it broke"
    # a later success clears it
    demo.post("/api/integrations/demo1/actions/flaky", json={"params": {"fail": False}})
    assert demo.get("/api/dashboard").get_json()["failing"] == []

    # someone who sees one zone counts only its hosts, and no plans of integrations it can't see
    viewer = make_user("vic", role="viewer", scopes=["zone:CA_*"])
    v = viewer.get("/api/dashboard").get_json()
    assert 0 < v["counts"]["hosts"] < 15  # (hosts with an address in CA count too)
    assert "notifications_failed" not in v["health"]
