"""Test a plugin without a running labdex: `labdex.testing`.

    from labdex.testing import FakeServer, PluginHarness

    def test_sync_plans_a_record():
        with FakeServer() as api, PluginHarness("path/to/my-plugin", settings={"url": api.url}, inventory=INVENTORY) as h:
            api.route("GET", "/records", json=[])
            changes = h.plan("sync")
            assert [c.name for c in changes] == ["nas.home.arpa"]
            h.apply("sync", changes)
            assert api.calls("POST", "/records")[0].json["name"] == "nas.home.arpa"

The harness gives the plugin a real `Context`: the real inventory (loaded from the YAML or dict you
pass, in a throw-away database), HTTP through the plugin's own `ctx.http`, state kept in memory, the
log collected in `h.logs`, and notifications collected in `h.notifications`. The manifest is checked
the way labdex checks it at install. The plugin's code runs in this process (not in the sandbox that
isolates community plugins), so what you test is its logic; the sandbox's rules are labdex's job.
"""
from __future__ import annotations

import json as _json
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

import yaml

from .plugins.base import Change, Context


# ------------------------------------------------------------------ a fake HTTP server
@dataclass
class Call:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes = b""

    @property
    def json(self):
        return _json.loads(self.body) if self.body else None


@dataclass
class _Route:
    method: str
    path: str
    status: int = 200
    json: object = None
    text: str | None = None
    handler: object = None
    headers: dict = field(default_factory=dict)


class FakeServer:
    """A small HTTP server on localhost for the API your plugin talks to.

    route("GET", "/api/items", json=[…]) answers with JSON; text= with plain text; status= sets the
    code (default 200); handler=fn(call) → (status, body) decides per request. A path ending in * matches
    everything below it; the first route registered for a request wins. Unrouted requests answer 404.
    Every request is kept: calls() lists them (all, or by method and path)."""

    def __init__(self):
        from werkzeug.serving import make_server
        from werkzeug.wrappers import Request

        self.routes: list[_Route] = []
        self.requests: list[Call] = []
        self._server = make_server("127.0.0.1", 0, Request.application(self._handle))
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def route(self, method: str, path: str, *, json=None, text=None, status=200, handler=None, headers=None) -> FakeServer:
        self.routes.append(_Route(method.upper(), path, status, json, text, handler, dict(headers or {})))
        return self

    def calls(self, method: str | None = None, path: str | None = None) -> list[Call]:
        return [c for c in self.requests if (method is None or c.method == method.upper()) and (path is None or c.path == path)]

    def _handle(self, req):
        from werkzeug.wrappers import Response
        call = Call(req.method, req.path, {k: v[0] if len(v) == 1 else v for k, v in parse_qs(req.query_string.decode()).items()},
                    dict(req.headers), req.get_data())
        self.requests.append(call)
        for r in self.routes:
            if r.method == call.method and (r.path == call.path or (r.path.endswith("*") and call.path.startswith(r.path[:-1]))):
                if r.handler is not None:
                    status, body = r.handler(call)
                    return Response(_json.dumps(body) if not isinstance(body, str | bytes) else body, status, mimetype="application/json")
                if r.text is not None:
                    return Response(r.text, r.status, headers=r.headers, mimetype="text/plain")
                return Response(_json.dumps(r.json if r.json is not None else {}), r.status, headers=r.headers, mimetype="application/json")
        return Response(_json.dumps({"error": f"no route for {call.method} {call.path}"}), 404, mimetype="application/json")

    def close(self) -> None:
        self._server.shutdown()

    def __enter__(self) -> FakeServer:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ------------------------------------------------------------------ the plugin harness
class PluginHarness:
    """Load a plugin folder and call it the way labdex does.

    PluginHarness(path, settings={…}, inventory=YAML text | dict | path to a YAML file | None,
                  host_settings={host: {…}} for per-host plugins, instance="test")
    Zones are needed to hold hosts: if the inventory has none, the example zone set is loaded."""

    def __init__(self, path, settings: dict | None = None, inventory=None, host_settings: dict | None = None, instance: str = "test"):
        self.path = Path(path)
        self.settings = dict(settings or {})
        self.host_settings = dict(host_settings or {})
        self._inventory = inventory
        self.instance = instance
        self.logs: list[str] = []
        self.notifications: list[dict] = []
        self.state: dict = {}
        self._tmp = None
        self._ctx_app = None
        self.spec = None
        self.plugin = None
        self.ctx = None

    # --- life cycle
    def __enter__(self) -> PluginHarness:
        import os

        from . import create_app, inventory as inv, zones
        from .models import db
        from .plugins import _load_dir
        self._tmp = tempfile.TemporaryDirectory(prefix="labdex-plugin-test-")
        tmp = Path(self._tmp.name)
        os.environ.setdefault("LABDEX_OFFICIAL_INDEX", "")
        app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp / 'test.db'}", "TESTING": True, "SECRET_KEY": "test",
                          "LABDEX_PLUGIN_DIR": str(tmp / "plugins"), "LABDEX_JOBS_SYNC": True, "LABDEX_SCHEDULER": False})
        self._ctx_app = app.app_context()
        self._ctx_app.push()
        data = self._inventory_dict()
        if data:
            inv.import_dict(data)
        else:
            zones.load_example()
        db.session.commit()
        spec = _load_dir(self.path, builtin=False, bundled=True)  # in this process, no sandbox
        if spec is None:
            raise FileNotFoundError(f"no plugin.yaml in {self.path}")
        if spec.error:
            raise AssertionError(f"{self.path.name}: {spec.error}")
        self.spec = spec
        self.ctx = HarnessContext(self, spec)
        cls = spec.plugin_class()
        self.plugin = cls(self.ctx) if cls else None
        return self

    def __exit__(self, *exc) -> None:
        from .models import db
        db.session.remove()
        self._ctx_app.pop()
        self._tmp.cleanup()

    def _inventory_dict(self) -> dict | None:
        raw = self._inventory
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        text = Path(raw).read_text(encoding="utf-8") if isinstance(raw, Path) or (isinstance(raw, str) and "\n" not in raw and Path(raw).is_file()) else raw
        return yaml.safe_load(text) or {}

    # --- calling the plugin
    def _call(self, prefix: str, name: str, *args):
        fn = getattr(self.plugin, f"{prefix}_{name}", None) if self.plugin else None
        if fn is None:
            raise AssertionError(f"{self.spec.id} has no {prefix}_{name}()")
        return fn(*args)

    def run(self, action: str, **params):
        """run_<action>(params) → its result."""
        return self._call("run", action, params)

    def plan(self, action: str, **params) -> list:
        """plan_<action>(params) → the list of Change."""
        out = self._call("plan", action, params) or []
        return [c if isinstance(c, Change) else Change(**c) for c in out]

    def apply(self, action: str, changes: list, **params):
        """apply_<action>(changes, params) → its result."""
        return self._call("apply", action, changes, params) or {}

    def export(self, action: str, **params):
        """export_<action>(params) → an ExportFile."""
        return self._call("export", action, params)

    def endpoint(self, endpoint_id: str, **params):
        return self._call("endpoint", endpoint_id, params)

    def page(self, page_id: str, **args):
        return self._call("page", page_id, args)

    def host_view(self, host: str) -> dict:
        """host_view(host) for one inventory host (with its `module_settings` from host_settings=)."""
        return self.plugin.host_view(self._host(host))

    def widget(self, widget_id: str, host: str | None = None):
        """widget_<id>(host): host is None on the dashboard."""
        return self._call("widget", widget_id, self._host(host) if host else None)

    def _host(self, name: str) -> dict:
        h = self.ctx.host(name)
        if h is None:
            raise AssertionError(f"no host '{name}' in the test inventory")
        return h


class HarnessContext(Context):
    """labdex's Context with in-memory state and notifications, hosts without selectors, and the log collected."""

    def __init__(self, harness: PluginHarness, spec):
        super().__init__(harness.instance, spec, harness.settings, harness.logs.append, integration=None)
        self._harness = harness

    @property
    def state(self) -> dict:
        return dict(self._harness.state)

    def set_state(self, key, value) -> None:
        self._harness.state[key] = value

    def notify(self, event, title, body="", link="", data=None) -> int:
        self._harness.notifications.append({"event": event, "title": title, "body": body, "link": link, "data": data or {}})
        return 0

    def hosts(self, include_offline=False) -> list[dict]:
        per_host = self._harness.host_settings
        return [h | {"module_settings": dict(per_host.get(h["name"], {}))} for h in self.inventory.hosts(include_offline)]
