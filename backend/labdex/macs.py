"""Where is this MAC? Switch forwarding tables (SNMP BRIDGE-MIB/Q-BRIDGE-MIB, a controller's
client list) say which port of which switch a MAC address was seen on.

Plugins report with ctx.discovery.record_macs(source, entries): each report replaces what that
source said before. An entry is {"switch": {"name", "ip", "mac"} or "host": <inventory host>,
"port", "mac", "vlan", "ip", "name"}.

A MAC is usually seen on several ports: where its device is plugged in, and on every uplink
between that switch and the one asking. The port where it's seen with the fewest other MACs is
the likely edge port, unless the inventory says that port is cabled to another switch.
"""
from __future__ import annotations

import ipaddress
import re

from . import store
from .models import DiscoveredHost, Host, Interface, Link, MacSeen, db, utcnow

MAC_RE = re.compile(r"^[0-9a-f]{2}([:\-.]?[0-9a-f]{2}){5}$", re.I)


def norm_mac(s: str) -> str:
    """aa:bb:cc:dd:ee:ff from any of the usual ways to write a MAC; '' if it isn't one."""
    h = re.sub(r"[^0-9a-f]", "", str(s or "").lower())
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 and MAC_RE.match(str(s).strip()) else ""


def record(source: str, entries: list[dict]) -> dict:
    """What `source` sees now (its whole table; the old one goes)."""
    from .cabling import _matcher
    match = _matcher()
    db.session.execute(db.delete(MacSeen).where(MacSeen.source == source[:120]))
    now, n, seen = utcnow(), 0, set()
    cache: dict = {}
    for e in entries or []:
        mac = norm_mac(e.get("mac", ""))
        port = str(e.get("port") or "").strip()[:64]
        if not mac or not port:
            continue
        sw = e.get("switch") or {}
        k = (e.get("host") or "", sw.get("name", ""), sw.get("ip", ""), sw.get("mac", ""))
        if k not in cache:
            cache[k] = store.find_host(str(e["host"])) if e.get("host") else match(sw.get("name", ""), sw.get("ip", ""), sw.get("mac", ""))
        h = cache[k]
        vlan = e.get("vlan")
        try:
            vlan = int(vlan) if vlan not in (None, "") else None
        except (TypeError, ValueError):
            vlan = None
        key = (h.id if h else sw.get("name"), port.lower(), mac, vlan)
        if key in seen:
            continue
        seen.add(key)
        db.session.add(MacSeen(source=source[:120], mac=mac, host=h, host_name=str(sw.get("name") or (h.name if h else ""))[:255], port=port,
                               vlan=vlan, ip=str(e.get("ip") or "")[:45], name=str(e.get("name") or "")[:255], seen_at=now))
        n += 1
    db.session.flush()
    return {"macs": n}


def _port_counts() -> dict:
    """How many MACs each switch port has: an uplink has many, an edge port one or a few."""
    out: dict = {}
    for host_id, host_name, port, cnt in db.session.execute(
            db.select(MacSeen.host_id, MacSeen.host_name, MacSeen.port, db.func.count(db.distinct(MacSeen.mac)))
            .group_by(MacSeen.host_id, MacSeen.host_name, MacSeen.port)):
        out[(host_id or host_name, port.lower())] = cnt
    return out


def _switch_ports() -> set:
    """Ports the inventory cables to another host that itself reports MAC tables (switch to switch)."""
    switches = {hid for (hid,) in db.session.execute(db.select(MacSeen.host_id).where(MacSeen.host_id.is_not(None)).distinct())}
    out = set()
    for lk in db.session.scalars(db.select(Link).where(Link.medium != "virtual")):
        if lk.a_host_id in switches and lk.b_host_id in switches:
            out.add((lk.a_host_id, lk.a_port.lower()))
            out.add((lk.b_host_id, lk.b_port.lower()))
    return out


def macs_for(q: str) -> tuple[list[str], list[dict]]:
    """The MACs a question means: a MAC itself, an IP address (inventory, Discovery, a switch's
    client list), or a host's name (its interfaces). Also says where each MAC came from."""
    q = str(q or "").strip()
    found: dict[str, dict] = {}
    mac = norm_mac(q)
    if mac:
        found[mac] = {"mac": mac, "why": "the address you asked for"}
    else:
        try:
            ip = str(ipaddress.ip_address(q))
        except ValueError:
            ip = ""
        if ip:
            for i in db.session.scalars(db.select(Interface).where(Interface.ip4 == ip)):
                if i.mac:
                    found.setdefault(i.mac.lower(), {"mac": i.mac.lower(), "why": f"{i.host.name} {i.label or ''} in the inventory".strip()})
            for d in db.session.scalars(db.select(DiscoveredHost).where(DiscoveredHost.ip == ip)):
                if d.mac:
                    found.setdefault(d.mac.lower(), {"mac": d.mac.lower(), "why": f"Discovery ({d.mac_source or d.source})"})
            for m in db.session.scalars(db.select(MacSeen).where(MacSeen.ip == ip)):
                found.setdefault(m.mac, {"mac": m.mac, "why": m.source})
        else:
            h = store.find_host(q)
            if h is not None:
                for i in h.interfaces:
                    if i.mac:
                        found.setdefault(i.mac.lower(), {"mac": i.mac.lower(), "why": f"{h.name} {i.label}".strip()})
    return list(found), list(found.values())


def where(q: str, visible: set | None = None) -> dict:
    """Where the MACs a question means were seen: per MAC its ports, the likely one first."""
    macs, why = macs_for(q)
    counts, uplinks = _port_counts(), _switch_ports()
    owners = {}
    for i in db.session.scalars(db.select(Interface).where(db.func.lower(Interface.mac).in_(macs))) if macs else []:
        owners[i.mac.lower()] = {"host": i.host.name, "uid": i.host.uid or "", "port": i.label or ""}
    results = []
    for m in macs:
        rows = []
        for s in db.session.scalars(db.select(MacSeen).where(MacSeen.mac == m)):
            if visible is not None and s.host is not None and s.host.name not in visible:
                continue
            k = (s.host_id or s.host_name, s.port.lower())
            cabled = _cable_peer(s.host_id, s.port) if s.host_id else None
            rows.append({"switch": s.host.name if s.host else s.host_name, "uid": s.host.uid if s.host else "", "known": s.host is not None,
                         "port": s.port, "vlan": s.vlan, "source": s.source, "seen_at": s.seen_at.isoformat() + "Z" if s.seen_at else "",
                         "macs_on_port": counts.get(k, 1), "uplink": (s.host_id, s.port.lower()) in uplinks, "cable": cabled,
                         "ip": s.ip, "name": s.name})
        rows.sort(key=lambda r: (r["uplink"], r["macs_on_port"], r["switch"], r["port"]))
        if rows:
            rows[0]["likely"] = not rows[0]["uplink"]
        owner = owners.get(m)
        if owner is not None and visible is not None and owner["host"] not in visible:
            owner = None
        results.append({"mac": m, "why": next((w["why"] for w in why if w["mac"] == m), ""), "owner": owner,
                        "vendor": _vendor(m), "seen": rows})
    return {"q": q, "results": results, "sources": sorted({s for (s,) in db.session.execute(db.select(MacSeen.source).distinct())})}


def _cable_peer(host_id: int, port: str) -> dict | None:
    p = port.lower()
    for lk in db.session.scalars(db.select(Link).where(db.or_(Link.a_host_id == host_id, Link.b_host_id == host_id), Link.medium != "virtual")):
        if lk.a_host_id == host_id and lk.a_port.lower() == p:
            return {"host": lk.b_host.name, "port": lk.b_port}
        if lk.b_host_id == host_id and lk.b_port.lower() == p:
            return {"host": lk.a_host.name, "port": lk.a_port}
    return None


def _vendor(mac: str) -> str:
    d = db.session.scalar(db.select(DiscoveredHost.vendor).where(db.func.lower(DiscoveredHost.mac) == mac, DiscoveredHost.vendor != ""))
    return d or ""


def location_of(h: Host) -> dict:
    """{interface label: the likely switch port} for a host's interfaces with a MAC."""
    out = {}
    for i in h.interfaces:
        if not i.mac:
            continue
        r = where(i.mac)["results"]
        best = next((s for s in r[0]["seen"] if s.get("likely")), None) if r and r[0]["seen"] else None
        if best:
            out[i.label or i.mac] = {k: best[k] for k in ("switch", "uid", "port", "vlan", "source", "seen_at")}
    return out
