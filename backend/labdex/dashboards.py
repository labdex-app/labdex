"""Dashboards: cards on a 12-column grid. The card types are drawn by the browser from data
labdex already serves (the start page's numbers, plugin widgets, services); links and notes
carry their own content, checked here so a dashboard can't smuggle in a javascript: link."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Dashboard, db, new_uid

COLS = 12
MAX_ITEMS = 60
MAX_LINKS = 60
TYPES = {
    "counts": "Hosts, networks, services, lint and Discovery in numbers",
    "approvals": "Plans waiting for approval",
    "failing": "Jobs that fail",
    "running": "Jobs running now",
    "changes": "The last changes",
    "upcoming": "Scheduled runs and change windows",
    "zones": "Hosts per zone",
    "widgets": "Every plugin widget",
    "widget": "One plugin widget",
    "links": "Links of your own",
    "services": "Links to the inventory's services, with their status",
    "note": "A note",
}
ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")

DEFAULT = [  # left: what needs you (approvals, failing, running, upcoming); right: what happened
    {"id": "counts", "type": "counts", "x": 0, "y": 0, "w": 12, "h": 3, "config": {}},
    {"id": "widgets", "type": "widgets", "x": 0, "y": 3, "w": 12, "h": 3, "config": {}},
    {"id": "approvals", "type": "approvals", "x": 0, "y": 6, "w": 7, "h": 3, "config": {}},
    {"id": "failing", "type": "failing", "x": 0, "y": 9, "w": 7, "h": 3, "config": {}},
    {"id": "running", "type": "running", "x": 0, "y": 12, "w": 7, "h": 3, "config": {}},
    {"id": "upcoming", "type": "upcoming", "x": 0, "y": 15, "w": 7, "h": 3, "config": {}},
    {"id": "changes", "type": "changes", "x": 7, "y": 6, "w": 5, "h": 9, "config": {}},
    {"id": "zones", "type": "zones", "x": 7, "y": 15, "w": 5, "h": 3, "config": {}},
]


def safe_url(u: str) -> bool:
    """Links open http(s) pages (or a path inside labdex); never javascript:, data: or the like."""
    u = (u or "").strip()
    if any(c in u for c in "\\\r\n\t"):  # "/\\host" is "//host" to a browser
        return False
    if u.startswith("/") and not u.startswith("//"):
        return True
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc)


def _s(v, n: int) -> str:
    return str(v or "").strip()[:n]


def clean_items(raw) -> tuple[list[dict], list[dict]]:
    """Validate a dashboard's cards: ([items], [errors])."""
    errors: list[dict] = []
    if not isinstance(raw, list):
        return [], [{"field": "items", "message": "a list of cards"}]
    if len(raw) > MAX_ITEMS:
        return [], [{"field": "items", "message": f"at most {MAX_ITEMS} cards"}]
    out, seen = [], set()
    for n, it in enumerate(raw):
        f = f"items[{n}]"
        if not isinstance(it, dict):
            errors.append({"field": f, "message": "a card"})
            continue
        typ = str(it.get("type", ""))
        if typ not in TYPES:
            errors.append({"field": f"{f}.type", "message": f"one of {', '.join(TYPES)}"})
            continue
        iid = str(it.get("id") or "").lower() or new_uid()
        if not ID_RE.match(iid) or iid in seen:
            iid = new_uid()
        seen.add(iid)
        try:
            w = max(1, min(COLS, int(it.get("w", 4))))
            h = max(1, min(40, int(it.get("h", 4))))
            x = max(0, min(COLS - w, int(it.get("x", 0))))
            y = max(0, min(1000, int(it.get("y", 0))))
        except (TypeError, ValueError):
            errors.append({"field": f, "message": "x, y, w and h are whole numbers"})
            continue
        cfg_in = it.get("config") or {}
        if not isinstance(cfg_in, dict):
            errors.append({"field": f"{f}.config", "message": "a mapping"})
            continue
        cfg: dict = {}
        if cfg_in.get("title"):
            cfg["title"] = _s(cfg_in["title"], 80)
        if typ == "widget":
            cfg["integration"], cfg["widget"] = _s(cfg_in.get("integration"), 64), _s(cfg_in.get("widget"), 64)
            if not cfg["integration"] or not cfg["widget"]:
                errors.append({"field": f"{f}.config", "message": "which integration and widget"})
        elif typ == "links":
            links = cfg_in.get("links") or []
            if not isinstance(links, list) or len(links) > MAX_LINKS:
                errors.append({"field": f"{f}.config.links", "message": f"a list of at most {MAX_LINKS} links"})
                links = []
            cfg["links"] = []
            for k, lk in enumerate(links):
                lk = lk if isinstance(lk, dict) else {}
                name, url = _s(lk.get("name"), 64), _s(lk.get("url"), 500)
                if not name and not url:
                    continue
                if not safe_url(url):
                    errors.append({"field": f"{f}.config.links[{k}].url", "message": "an http(s) address, or a path in labdex"})
                cfg["links"].append({"name": name or url, "url": url, "description": _s(lk.get("description"), 120)})
            cfg["columns"] = max(1, min(6, int(cfg_in.get("columns") or 3))) if str(cfg_in.get("columns") or "3").isdigit() else 3
        elif typ == "services":
            cfg["tag"], cfg["zone"], cfg["category"] = _s(cfg_in.get("tag"), 32), _s(cfg_in.get("zone"), 48).upper(), _s(cfg_in.get("category"), 16)
        elif typ == "note":
            cfg["text"] = _s(cfg_in.get("text"), 4000)
        out.append({"id": iid, "type": typ, "x": x, "y": y, "w": w, "h": h, "config": cfg})
    return out, errors


def visible(principal) -> list[Dashboard]:
    me = principal.user.username if principal.user else None
    q = db.select(Dashboard).order_by(Dashboard.owner != "", Dashboard.position, Dashboard.id)
    return [d for d in db.session.scalars(q) if d.owner == "" or d.owner == me]


def can_edit(d: Dashboard, principal) -> bool:
    if d.owner == "":
        return bool(principal.is_admin)
    return bool(principal.user and principal.user.username == d.owner)


def to_dict(d: Dashboard, principal, with_items=True) -> dict:
    out = {"uid": d.uid, "name": d.name, "shared": d.owner == "", "can_edit": can_edit(d, principal),
           "updated_by": d.updated_by or "", "updated_at": d.updated_at.isoformat() + "Z" if d.updated_at else None}
    if with_items:
        out["items"] = list(d.items or [])
    return out


def ensure_overview() -> None:
    """The shared start page, when there's no shared dashboard at all (a wiped demo, say)."""
    import copy
    from .models import Dashboard, db
    if db.session.scalar(db.select(Dashboard.id).where(Dashboard.owner == "").limit(1)) is None:
        db.session.add(Dashboard(name="Overview", owner="", position=0, items=copy.deepcopy(DEFAULT), updated_by="labdex"))
