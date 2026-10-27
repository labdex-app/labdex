"""Plugin widgets: small, declarative views on the dashboard and on host pages.

A plugin declares them in plugin.yaml:

    widgets:
      - {id: last, label: Last backup, where: dashboard, size: small, refresh: 300}
      - {id: ports, label: Open ports, where: host}

and returns data from `widget_<id>(self, host)` (host is the host dict for host
widgets, None on the dashboard). labdex draws a fixed set of kinds and never runs
plugin code in the browser:

    {"kind": "stat", "value": 42, "unit": "ms", "sub": "p95 over 1 h", "tone": "ok|warn|err|"}
    {"kind": "status", "items": [{"label": "…", "state": "up|down|warn|unknown", "detail": "…", "link": "…"}]}
    {"kind": "list", "items": [{"label": "…", "value": "…", "tone": "…", "link": "…"}]}
    {"kind": "table", "columns": ["…"], "rows": [["…", 1]]}
    {"kind": "sparkline", "points": [1.0, 2.5, …], "value": 2.5, "unit": "%", "sub": "last 24 h", "min": 0, "max": 100}

Anything else, or a field of the wrong type, is dropped. Results are cached for the
widget's `refresh` seconds (default 60) and each call gets a few seconds.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from flask import current_app

from .models import Integration, db

KINDS = ("stat", "status", "list", "table", "sparkline")
TONES = ("", "ok", "warn", "err")
STATES = ("up", "down", "warn", "unknown")
TIMEOUT = 8
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="labdex-widget")


def _s(v, n=200) -> str:
    return "" if v is None else str(v)[:n]


def _link(v) -> str:
    v = _s(v, 500)
    return v if v.startswith(("/", "https://", "http://")) else ""


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def clean(d) -> dict:
    """Only what labdex knows how to draw."""
    if not isinstance(d, dict) or d.get("kind") not in KINDS:
        return {"kind": "error", "error": "the plugin returned no widget labdex can draw"}
    k = d["kind"]
    out: dict = {"kind": k}
    if d.get("sub"):
        out["sub"] = _s(d["sub"])
    if d.get("link"):
        out["link"] = _link(d["link"])
    if k == "stat":
        out["value"] = d["value"] if isinstance(d.get("value"), (int, float)) and not isinstance(d.get("value"), bool) else _s(d.get("value"), 40)
        out["unit"] = _s(d.get("unit"), 12)
        out["tone"] = d.get("tone") if d.get("tone") in TONES else ""
    elif k == "status":
        out["items"] = [{"label": _s(i.get("label"), 80), "state": i.get("state") if i.get("state") in STATES else "unknown",
                         "detail": _s(i.get("detail")), "link": _link(i.get("link"))}
                        for i in (d.get("items") or [])[:50] if isinstance(i, dict)]
    elif k == "list":
        out["items"] = [{"label": _s(i.get("label"), 120), "value": _s(i.get("value"), 80),
                         "tone": i.get("tone") if i.get("tone") in TONES else "", "link": _link(i.get("link"))}
                        for i in (d.get("items") or [])[:50] if isinstance(i, dict)]
    elif k == "table":
        cols = [_s(c, 40) for c in (d.get("columns") or [])[:8]]
        out["columns"] = cols
        out["rows"] = [[c if isinstance(c, (int, float)) and not isinstance(c, bool) else _s(c, 120) for c in r[:len(cols)]]
                       for r in (d.get("rows") or [])[:100] if isinstance(r, (list, tuple))]
    elif k == "sparkline":
        pts = [_num(p) for p in (d.get("points") or [])[:500]]
        out["points"] = [p for p in pts if p is not None]
        for key in ("value", "min", "max"):
            v = _num(d.get(key))
            if v is not None:
                out[key] = v
        out["unit"] = _s(d.get("unit"), 12)
    return out


def declared(where: str):
    """(integration, spec, widget) for every enabled instance with a widget for this place."""
    reg = current_app.extensions["labdex"]["registry"]
    for integ in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
        spec = reg.get(integ.plugin_id)
        if spec is None or spec.error:
            continue
        for w in spec.widgets:
            if w.get("where", "dashboard") == where:
                yield integ, spec, w


def _call(app, integ_id: int, wid: str, host: dict | None) -> dict:
    from .plugins import resolved_settings
    from .plugins.base import Context
    with app.app_context():
        try:
            integ = db.session.get(Integration, integ_id)
            spec = app.extensions["labdex"]["registry"].get(integ.plugin_id)
            settings = resolved_settings(spec, integ.settings or {}, app.extensions["labdex"]["vault"])
            ctx = Context(integ.name, spec, settings, lambda m: None, integration=integ)
            cls = spec.plugin_class()
            fn = getattr(cls(ctx), f"widget_{wid}", None) if cls else None
            if fn is None:
                return {"kind": "error", "error": f"plugin has no widget_{wid}()"}
            return clean(fn(host))
        except Exception as exc:  # a broken widget never breaks the page
            return {"kind": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
        finally:
            db.session.remove()


def render(items: list[tuple], host: dict | None = None) -> list[dict]:
    """Run the widgets (cached, in parallel, each with a time limit)."""
    app = current_app._get_current_object()
    now = time.monotonic()
    out, todo = [], []
    for integ, spec, w in items:
        key = (integ.id, w["id"], host["name"] if host else None)
        meta = {"integration": integ.name, "plugin": spec.id, "id": w["id"], "label": w.get("label") or w["id"],
                "size": w.get("size", "small") if w.get("size") in ("small", "wide") else "small"}
        with _lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < int(w.get("refresh") or 60):
            out.append(meta | {"data": hit[1]})
            continue
        fut = _pool.submit(_call, app, integ.id, w["id"], host)
        todo.append((meta, key, fut))
        out.append(meta)
    for meta, key, fut in todo:
        try:
            data = fut.result(timeout=TIMEOUT)
        except FutureTimeout:
            data = {"kind": "error", "error": f"no answer within {TIMEOUT} s"}
        if data.get("kind") != "error":
            with _lock:
                _cache[key] = (time.monotonic(), data)
        meta["data"] = data
    return out


def clear() -> None:
    with _lock:
        _cache.clear()
