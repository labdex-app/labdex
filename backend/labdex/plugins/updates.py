"""Plugin updates: holding a version, the update policy, and the regular check.

Settings (instance settings, kept through an inventory import):
    plugin_holds         {"plugin id": "version"}: stay on that version, never offered an update
    plugin_update_mode   "notify" (default): tell, install nothing
                         "official": install updates signed with the labdex project key by
                                     themselves; anything else is only announced
                         "off": no check, no notices (the Marketplace still shows updates)
    plugin_updates_seen  {"plugin id": "version"}: what was announced already, so each
                         update (and each withdrawn version) is announced once

The check runs from the scheduler every few hours: it reads the enabled repositories,
announces new updates and withdrawn installed versions (notification events plugins.update,
plugins.updated, plugins.update_failed and plugins.withdrawn) and, in "official" mode, installs what qualifies:
signed with the project key, compatible, not withdrawn, not held.
"""
from __future__ import annotations

import json
from datetime import timedelta

from flask import current_app

from ..models import Setting, db, utcnow

HOLDS, MODE, SEEN, LAST = "plugin_holds", "plugin_update_mode", "plugin_updates_seen", "plugin_updates_checked"
MODES = ("notify", "official", "off")
EVERY = timedelta(hours=6)


def _get(key: str, default):
    row = db.session.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return json.loads(row.value) if isinstance(default, dict) else row.value
    except ValueError:
        return default


def _put(key: str, value) -> None:
    row = db.session.get(Setting, key)
    text = json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)
    if row is None:
        db.session.add(Setting(key=key, value=text))
    else:
        row.value = text


def holds() -> dict[str, str]:
    return {str(k): str(v) for k, v in _get(HOLDS, {}).items()}


def hold(pid: str, version: str | None) -> None:
    h = holds()
    if version:
        h[pid] = version
    else:
        h.pop(pid, None)
    _put(HOLDS, h)


def mode() -> str:
    m = _get(MODE, "notify")
    return m if m in MODES else "notify"


def set_mode(m: str) -> None:
    if m not in MODES:
        raise ValueError(f"one of {', '.join(MODES)}")
    _put(MODE, m)


def pending(market: dict | None = None) -> list[dict]:
    """Marketplace entries with an update to offer (not held)."""
    from .installer import marketplace
    m = market or marketplace(fetch=True)
    return [p for p in m["plugins"] if p.get("update")]


def check(force: bool = False) -> dict:
    """Announce what's new, install official updates in "official" mode. Returns what it did."""
    from .. import audit, notify
    from .installer import InstallError, confirm, install_from_repository, marketplace
    from ..models import PluginRepository
    md = mode()
    last = _get(LAST, "")
    if md == "off" and not force:
        return {"mode": md, "skipped": True}
    if not force and last:
        from datetime import datetime
        try:
            if utcnow() - datetime.fromisoformat(last) < EVERY:
                return {"mode": md, "skipped": True}
        except ValueError:
            pass
    _put(LAST, utcnow().isoformat())
    db.session.commit()
    market = marketplace(fetch=True)
    seen = _get(SEEN, {})
    done = {"mode": md, "announced": [], "installed": [], "failed": [], "withdrawn": []}
    for p in market["plugins"]:
        if p.get("installed") and p.get("withdrawn"):
            key = f"withdrawn:{p['id']}"
            if seen.get(key) != p["installed_version"]:
                seen[key] = p["installed_version"]
                done["withdrawn"].append(p["id"])
                notify.emit("plugins.withdrawn", f"{p['name']} {p['installed_version']} was withdrawn",
                            f"{p['withdrawn']}. Update or roll back in the Marketplace.", link=f"/plugins?tab=market&plugin={p['id']}")
        if not p.get("update"):
            continue
        target = p["repo_version"]
        auto = md == "official" and p.get("official_signed") and p.get("repository_id")
        if auto:
            try:
                repo = db.session.get(PluginRepository, p["repository_id"])
                info = install_from_repository(repo, p["id"], target)
                if info["trust"]["status"] != "official":
                    raise InstallError("not signed with the labdex project key")
                confirm(info["token"], accept_unsigned=False)
                audit.record("update", "plugin", p["id"], {"version": p["installed_version"]}, {"version": target},
                             summary=f"updated {p['name']} {p['installed_version']} → {target} by itself (official update)")
                db.session.commit()
                done["installed"].append(p["id"])
                notify.emit("plugins.updated", f"{p['name']} updated to {target}", p.get("changes") or "",
                            link=f"/plugins?tab=market&plugin={p['id']}")
                seen[p["id"]] = target
                continue
            except Exception as exc:  # one plugin failing must not stop the others
                db.session.rollback()
                current_app.logger.warning("updating %s failed: %s", p["id"], exc)
                done["failed"].append(p["id"])
                notify.emit("plugins.update_failed", f"Updating {p['name']} to {target} failed", str(exc)[:500],
                            link=f"/plugins?tab=market&plugin={p['id']}")
        if seen.get(p["id"]) != target:
            seen[p["id"]] = target
            done["announced"].append(p["id"])
            notify.emit("plugins.update", f"{p['name']} {target} is available", (p.get("changes") or "")[:1500],
                        link=f"/plugins?tab=market&plugin={p['id']}")
    _put(SEEN, seen)
    db.session.commit()
    return done
