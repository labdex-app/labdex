"""Host status from monitoring sources (VictoriaMetrics, Beszel, Uptime Kuma, …).

Each status plugin instance reports what it knows about hosts with
`ctx.report_status({host: {"state": "up|down|unknown", "detail": "…", "data": {…}}})`.
A host's combined state: down if any fresh source says down, up if at least one says
up, otherwise what Discovery last saw (scans, leases, ARP). A report older than
`status_stale_minutes` counts as unknown. When the combined state of an active host
turns from up to down or back, a host.down / host.up notification goes out.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import timedelta

from .models import Host, HostHealth, HostStatus, Integration, db, utcnow

STATES = ("up", "down", "unknown")


def stale_after() -> timedelta:
    from . import naming
    try:
        return timedelta(minutes=max(int(naming.get_setting("status_stale_minutes") or 15), 1))
    except ValueError:
        return timedelta(minutes=15)


# ------------------------------------------------------------------ matching
def host_matcher():
    """A function: an address, host name, FQDN, URL or host:port → inventory host name (or None)."""
    from . import naming
    domain = naming.get_setting("domain").lower()
    by_name, by_ip = {}, {}
    for h in db.session.scalars(db.select(Host)):
        names = {h.name.lower(), f"{h.name}.{domain}".lower()}
        names |= {f.lower() for f in (h.fun_names or []) if f}
        for i in h.interfaces:
            if i.network is not None:
                names.add(f"{h.name}.{i.network.name}.{domain}".lower())
            for a in i.all_addresses:
                if a:
                    by_ip[a] = h.name
        for n in names:
            by_name.setdefault(n, h.name)

    from .generators import dns
    for r in dns.records():  # every generated name, CNAMEs and fun names included
        if r.host:
            by_name.setdefault(r.fqdn(domain).lower().rstrip("."), r.host)

    def match(text: str) -> str | None:
        t = str(text or "").strip().lower()
        if not t:
            return None
        t = re.sub(r"^[a-z][a-z0-9+.-]*://", "", t).split("/", 1)[0]
        if t.startswith("["):  # [v6]:port
            t = t[1:].split("]", 1)[0]
        elif t.count(":") == 1:
            t = t.split(":", 1)[0]
        try:
            return by_ip.get(str(ipaddress.ip_address(t)))
        except ValueError:
            pass
        return by_name.get(t) or by_name.get(t.rstrip("."))
    return match


# ------------------------------------------------------------------ reporting
def report(integ: Integration, results: dict[str, dict]) -> dict:
    """Replace what this source says. Returns {"hosts": n, "changes": [(host, old, new)]}."""
    now = utcnow()
    rows = {r.host: r for r in db.session.scalars(db.select(HostStatus).where(HostStatus.integration_id == integ.id))}
    known = set(db.session.scalars(db.select(Host.name)))
    for name, r in results.items():
        if name not in known:
            continue
        state = r.get("state") if r.get("state") in STATES else "unknown"
        row = rows.pop(name, None)
        if row is None:
            row = HostStatus(host=name, integration_id=integ.id, state=state, changed_at=now)
            db.session.add(row)
        elif row.state != state:
            row.changed_at = now
        row.state, row.detail, row.data, row.checked_at = state, str(r.get("detail") or "")[:300], r.get("data") or {}, now
    for row in rows.values():  # no longer known to this source
        db.session.delete(row)
    db.session.commit()
    return {"hosts": len(results), "changes": reconcile()}


def sources() -> dict[str, list[dict]]:
    """host → what each source says (stale reports as unknown)."""
    cutoff = utcnow() - stale_after()
    out: dict[str, list[dict]] = {}
    for r in db.session.scalars(db.select(HostStatus).join(Integration).where(Integration.enabled.is_(True))):
        fresh = r.checked_at >= cutoff
        out.setdefault(r.host, []).append({
            "integration": r.integration.name, "plugin": r.integration.plugin_id,
            "state": r.state if fresh else "unknown", "detail": r.detail if fresh else f"no report since {r.checked_at.isoformat()}Z",
            "data": r.data or {}, "checked_at": r.checked_at.isoformat() + "Z", "since": r.changed_at.isoformat() + "Z"})
    return out


def combine(srcs: list[dict], discovered: str | None) -> str:
    states = [s["state"] for s in srcs]
    if "down" in states:
        return "down"
    if "up" in states:
        return "up"
    return discovered or "unknown"


def states() -> dict[str, dict]:
    """host → {"live": up|down|unknown, "by": "sources"|"discovery"|None, "sources": {integration: state}}."""
    from . import discovery
    disc = discovery.live_status()
    src = sources()
    out = {}
    for name in set(disc) | set(src):
        s = src.get(name, [])
        fresh = [x for x in s if x["state"] != "unknown"]
        out[name] = {"live": combine(s, disc.get(name)), "by": "sources" if fresh else "discovery" if name in disc else None,
                     "sources": {x["integration"]: x["state"] for x in s}}
    return out


def reconcile() -> list[tuple]:
    """Compare with the last combined state; notify active hosts that went down or came back."""
    from . import notify
    now = utcnow()
    cur = states()
    hosts = {h.name: h for h in db.session.scalars(db.select(Host).where(Host.name.in_(list(cur))))} if cur else {}
    seen = {r.host: r for r in db.session.scalars(db.select(HostHealth))}
    changes = []
    for name, st in cur.items():
        if st["by"] != "sources":
            continue  # only live sources drive notifications; Discovery is a fallback for display
        new = st["live"]
        row = seen.get(name)
        if row is None:
            db.session.add(HostHealth(host=name, state=new, since=now))
            continue
        if row.state == new:
            continue
        old = row.state
        row.state, row.since = new, now
        changes.append((name, old, new))
        h = hosts.get(name)
        if h is not None and h.status == "active" and {old, new} == {"up", "down"}:
            srcs = ", ".join(f"{k}: {v}" for k, v in sorted(st["sources"].items()))
            notify.emit(f"host.{new}", f"{h.fun or name} is {new}", f"{name} ({srcs})", f"/hosts/{name}",
                        {"host": name, "state": new, "sources": st["sources"]}, commit=False)
    db.session.commit()
    return changes


def rename(old: str, new: str) -> None:
    for r in db.session.scalars(db.select(HostStatus).where(HostStatus.host == old)):
        r.host = new
    hh = db.session.get(HostHealth, old)
    if hh is not None:
        db.session.delete(hh)


def forget(name: str) -> None:
    db.session.execute(db.delete(HostStatus).where(HostStatus.host == name))
    db.session.execute(db.delete(HostHealth).where(HostHealth.host == name))
