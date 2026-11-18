"""Patch state: which hosts have updates waiting, which need a reboot, when each was last patched.

Sources report what they see, one row per host and source:

* an **agent**: `tools/labdex-patch-report` on the host (apt, dnf, Proxmox), from cron or Ansible,
  posting to `POST /api/patches` with an API token;
* an **integration**: a plugin calls `ctx.report_patches({host: {...}})` (Proxmox nodes, Synology …).

`summary(host)` merges the sources of a host: the most recent report counts, also for the reboot
(a patch run that rebooted the host clears what an older report said). A report older than STALE is shown as stale, not as up to date.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from . import sites, store
from .models import Host, HostPatch, db, utcnow

STALE = timedelta(days=3)
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9 ._:-]{0,63}$", re.I)
MAX_PACKAGES = 500


def _int(v, field: str, errors: list) -> int:
    try:
        n = int(v or 0)
        if n < 0:
            raise ValueError
        return n
    except (TypeError, ValueError):
        errors.append({"field": field, "message": "a whole number, 0 or more"})
        return 0


def _when(v, field: str, errors: list) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, int | float):
        return datetime.fromtimestamp(v, UTC).replace(tzinfo=None)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        errors.append({"field": field, "message": "an ISO date and time (2026-11-18T09:00:00Z)"})
        return None
    return d.replace(tzinfo=None) - (d.utcoffset() or timedelta(0)) if d.tzinfo else d


def clean(data: dict, prefix: str = "") -> tuple[dict, list]:
    """A report as the API or a plugin sends it → (row values, errors)."""
    errors: list = []
    pkgs = []
    for i, p in enumerate((data.get("packages") or [])[:MAX_PACKAGES]):
        if isinstance(p, str):
            p = {"name": p}
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            errors.append({"field": f"{prefix}packages[{i}]", "message": "a package: {name, from, to, security}"})
            continue
        pkgs.append({"name": str(p["name"])[:128], "from": str(p.get("from") or "")[:64], "to": str(p.get("to") or "")[:64],
                     "security": bool(p.get("security"))})
    pending = _int(data.get("pending", len(pkgs)), f"{prefix}pending", errors)
    security = _int(data.get("security", sum(p["security"] for p in pkgs)), f"{prefix}security", errors)
    out = {"pending": max(pending, len(pkgs)), "security": security, "packages": pkgs, "reboot": bool(data.get("reboot")),
           "os": str(data.get("os") or "")[:64], "kernel": str(data.get("kernel") or "")[:64],
           "patched_at": _when(data.get("patched_at"), f"{prefix}patched_at", errors), "error": str(data.get("error") or "")[:300]}
    return out, errors


def report(source: str, results: dict[str, dict], replace: bool = False, site: str | None = None) -> dict:
    """Save what `source` says: {host: {pending, security, packages, reboot, os, kernel, patched_at, error}}.
    Unknown hosts (and with `site`, hosts of other sites) are skipped. With `replace`, hosts this
    source no longer lists lose its row."""
    source = str(source or "agent").strip()[:64]
    if not SOURCE_RE.match(source):
        raise store.ValidationError([{"field": "source", "message": "letters, digits, space . _ : -"}])
    q = db.select(Host.name).where(Host.name.in_(list(results)))
    known = set(db.session.scalars(q.where(Host.site == site) if site else q))
    rows = {r.host: r for r in db.session.scalars(db.select(HostPatch).where(HostPatch.source == source))}
    errors, saved, skipped, newly = [], [], [], []
    now = utcnow()
    for name, data in results.items():
        if name not in known:
            skipped.append(name)
            continue
        values, errs = clean(data if isinstance(data, dict) else {}, f"{name}.")
        if errs:
            errors += errs
            continue
        row = rows.pop(name, None)
        if row is None:
            row = HostPatch(host=name, source=source)
            db.session.add(row)
        was_reboot, was_security = row.reboot, row.security or 0
        for k, v in values.items():
            setattr(row, k, v)
        row.checked_at = now
        if values["reboot"] and not was_reboot or values["security"] and not was_security:
            newly.append(name)
        saved.append(name)
    if errors:
        db.session.rollback()
        raise store.ValidationError(errors)
    if replace:
        for row in rows.values():
            db.session.delete(row)
    _notify(newly)
    db.session.commit()
    return {"saved": saved, "skipped": skipped}


def _notify(names: list[str]) -> None:
    if not names:
        return
    from . import notify
    for name in names:
        s = summary(name)
        what = " and ".join(x for x in (f"{s['security']} security updates" if s["security"] else "", "a reboot" if s["reboot"] else "") if x)
        if what:
            notify.emit("host.patches", f"{name} needs {what}", f"{s['pending']} updates waiting", f"/hosts/{name}",
                        {"host": name, **{k: s[k] for k in ("pending", "security", "reboot")}}, commit=False)


def _row(r: HostPatch) -> dict:
    return {"source": r.source, "pending": r.pending, "security": r.security, "packages": r.packages or [], "reboot": r.reboot,
            "os": r.os, "kernel": r.kernel, "patched_at": _iso(r.patched_at), "checked_at": _iso(r.checked_at), "error": r.error,
            "stale": r.checked_at < utcnow() - STALE}


def _iso(d) -> str | None:
    return d.isoformat(timespec="seconds") + "Z" if d else None


def _merge(rows: list[HostPatch]) -> dict:
    if not rows:
        return {"state": "unknown", "pending": 0, "security": 0, "reboot": False, "os": "", "patched_at": None, "checked_at": None, "sources": []}
    fresh = [r for r in rows if r.checked_at >= utcnow() - STALE]
    latest = max(fresh or rows, key=lambda r: r.checked_at)
    patched = [r.patched_at for r in rows if r.patched_at]
    reboot = bool(fresh) and latest.reboot
    state = ("stale" if not fresh else "error" if latest.error else "security" if latest.security else "reboot" if reboot
             else "pending" if latest.pending else "current")
    return {"state": state, "pending": latest.pending, "security": latest.security, "reboot": reboot, "os": latest.os,
            "kernel": latest.kernel, "patched_at": _iso(max(patched)) if patched else None, "checked_at": _iso(latest.checked_at),
            "sources": [_row(r) for r in sorted(rows, key=lambda r: r.source)]}


def summary(host: str) -> dict:
    return _merge(list(db.session.scalars(db.select(HostPatch).where(HostPatch.host == host))))


def overview() -> dict[str, dict]:
    """host → merged state, for the hosts in scope (sites.scoped)."""
    names = set(db.session.scalars(sites.hosts(db.select(Host.name))))
    by: dict[str, list] = {}
    for r in db.session.scalars(db.select(HostPatch).where(HostPatch.host.in_(names))):
        by.setdefault(r.host, []).append(r)
    return {n: _merge(by.get(n, [])) for n in names}


def counts(states: dict[str, dict]) -> dict:
    """Hosts per state; `reboot` counts every host that needs one, also those whose state is security."""
    out = {"current": 0, "pending": 0, "security": 0, "reboot": 0, "stale": 0, "error": 0, "unknown": 0}
    for s in states.values():
        out[s["state"]] += 1
        if s["reboot"] and s["state"] != "reboot":
            out["reboot"] += 1
    return out


def rename(old: str, new: str) -> None:
    for r in db.session.scalars(db.select(HostPatch).where(HostPatch.host == old)):
        r.host = new


def forget(name: str) -> None:
    db.session.execute(db.delete(HostPatch).where(HostPatch.host == name))
