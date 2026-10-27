"""v0.7.1: declarative plugin widgets."""
import pytest

from labdex import widgets
from labdex.models import DiscoveredService, db
from labdex.plugins import installer

from .test_plugins import make_instance, run, write_plugin

DEMO = """
id: demo
name: Demo
widgets:
  - {id: temp, label: Temperature, where: dashboard, size: wide}
  - {id: bad, label: Broken}
  - {id: weird, label: Weird}
  - {id: slow, label: Slow}
  - {id: load, label: Load, where: host}
"""
CODE = """
import time
from labdex.plugins.base import Plugin as Base
class Plugin(Base):
    calls = 0
    def widget_temp(self, host):
        Plugin.calls += 1
        return {"kind": "sparkline", "points": [20, 21.5, "22", None, "x"], "unit": "°C", "sub": "last hour", "link": "javascript:alert(1)"}
    def widget_bad(self, host):
        raise RuntimeError("sensor offline")
    def widget_weird(self, host):
        return {"kind": "html", "html": "<script>"}
    def widget_slow(self, host):
        time.sleep(3)
        return {"kind": "stat", "value": 1}
    def widget_load(self, host):
        return {"kind": "status", "items": [{"label": host["name"], "state": "up", "detail": "load 0.3"}, {"label": "x", "state": "on fire"}]}
"""


@pytest.fixture
def demo(loaded, admin, monkeypatch):
    monkeypatch.setattr(widgets, "TIMEOUT", 1)
    widgets.clear()
    write_plugin(loaded, "demo", DEMO, CODE)
    installer.reload_registry(loaded)
    admin.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
    admin.put("/api/integrations/demo1", json={"enabled": True})
    return admin


def test_dashboard_widgets(demo, make_user, monkeypatch):
    from labdex.plugins import sandbox
    asked = []
    real = sandbox.call
    monkeypatch.setattr(sandbox, "call", lambda spec, ctx, method, *a, **kw: (asked.append(method), real(spec, ctx, method, *a, **kw))[1])
    ws = {w["id"]: w for w in demo.get("/api/widgets").get_json()}
    assert set(ws) == {"temp", "bad", "weird", "slow"}
    t = ws["temp"]
    assert t["size"] == "wide" and t["data"] == {"kind": "sparkline", "points": [20, 21.5, 22.0], "unit": "°C", "sub": "last hour", "link": ""}
    assert ws["bad"]["data"] == {"kind": "error", "error": "RuntimeError: sensor offline"}
    assert ws["weird"]["data"]["kind"] == "error"
    assert "no answer" in ws["slow"]["data"]["error"]
    # cached: the plugin isn't asked again within its refresh time
    demo.get("/api/widgets")
    assert asked.count("widget_temp") == 1  # (an unsigned plugin: each call is an isolated process)
    # people who can't see the instance don't get its widgets
    v = make_user("v", role="viewer", scopes=["zone:CA_*"])
    assert v.get("/api/widgets").get_json() == []


def test_host_widgets(demo):
    ws = demo.get("/api/hosts/srv-vm-dns-01/widgets").get_json()
    assert [w["id"] for w in ws] == ["load"]
    assert ws[0]["data"]["items"] == [{"label": "srv-vm-dns-01", "state": "up", "detail": "load 0.3", "link": ""},
                                      {"label": "x", "state": "unknown", "detail": "", "link": ""}]
    # only where the instance applies
    demo.put("/api/integrations/demo1", json={"selectors": "zone:CA_*"})
    widgets.clear()
    assert demo.get("/api/hosts/srv-vm-dns-01/widgets").get_json() == []
    assert demo.get("/api/hosts/nope/widgets").status_code == 404


def test_builtin_widgets(admin, loaded, tmp_path):
    widgets.clear()
    make_instance(admin, "backup", "bk", {"path": str(tmp_path / "b")})
    w = next(w for w in admin.get("/api/widgets").get_json() if w["integration"] == "bk")
    assert w["data"]["value"] == "none" and w["data"]["tone"] == "err"
    run(admin, "bk", "backup")
    widgets.clear()
    w = next(w for w in admin.get("/api/widgets").get_json() if w["integration"] == "bk")
    assert w["data"]["value"] == 0 and w["data"]["unit"] == "h ago" and w["data"]["tone"] == "warn" and "NOT encrypted" in w["data"]["sub"]

    make_instance(admin, "nmap", "scan", {})
    db.session.add_all([DiscoveredService(ip="10.10.100.53", port=53, proto="udp", service="domain"),
                        DiscoveredService(ip="10.10.100.53", port=8080, proto="tcp", service="http-proxy")])
    db.session.commit()
    w = admin.get("/api/hosts/srv-vm-dns-01/widgets").get_json()[0]
    items = {i["label"]: i for i in w["data"]["items"]}
    assert items["53/udp"]["value"] == "dns" and items["53/udp"]["tone"] == "ok"
    assert items["8080/tcp"]["tone"] == "warn" and w["data"]["sub"] == "1 not documented as a service"


def test_manifest_checks(loaded):
    from pathlib import Path
    from labdex.plugins import _validate
    assert "where must be" in _validate({"id": "xx", "widgets": [{"id": "a", "where": "sidebar"}]}, Path("."))
    assert "duplicate" in _validate({"id": "xx", "widgets": [{"id": "a"}, {"id": "a"}]}, Path("."))
    assert widgets.clean({"kind": "table", "columns": ["a", "b"], "rows": [[1, "x", "extra"], "bad"]}) == {
        "kind": "table", "columns": ["a", "b"], "rows": [[1, "x"]]}
