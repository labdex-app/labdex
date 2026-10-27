"""What's actually on the network, compared with the inventory.

Scanners (nmap) and neighbour sources (OPNsense ARP, UniFi, …) write here
through the plugin context. The API classifies every open port:

    documented      host is in the inventory and declares this port as a service
    undocumented    host is in the inventory, but the port isn't declared
    unknown-host    the address isn't in the inventory at all

and lists declared services that a recent scan didn't find (missing).
"""
from __future__ import annotations

from datetime import timedelta

from . import naming, rbac
from .models import DiscoveredHost, DiscoveredService, Host, Interface, db, utcnow


def _set_mac(dh: DiscoveredHost, mac: str, source: str, vendor: str = "") -> None:
    mac = (mac or "").lower()
    if not mac or mac == "00:00:00:00:00:00" or mac.startswith("ff:ff:ff"):
        return
    if mac != dh.mac or not dh.mac_source:
        dh.mac, dh.mac_source = mac, source
    if vendor:
        dh.vendor = vendor


def record_scan(source: str, scanned: set[str], hosts: list[dict], neighbours: dict[str, str] | None = None,
                probed: dict[str, list] | None = None) -> dict:
    """Store one scan. `hosts`: [{ip, hostname, mac?, vendor?, services: [{port, proto, service, product, version}]}].
    `neighbours`: {ip: mac} from the scanning machine's ARP table, used where the scan had no MAC.
    `probed`: {ip: [[port, proto], …]} ports the scan asked for on purpose (the declared services):
    those that didn't answer are stored as closed, so "not answering" means it was really checked.
    Ports on scanned addresses that aren't in the results are marked closed."""
    now = utcnow()
    seen_services = set()
    neighbours = neighbours or {}
    for h in hosts:
        dh = db.session.scalar(db.select(DiscoveredHost).where(DiscoveredHost.ip == h["ip"])) or DiscoveredHost(ip=h["ip"], first_seen=now)
        if h.get("mac") and not source.startswith("nmap:"):
            _set_mac(dh, h["mac"], source, h.get("vendor", ""))
        elif h["ip"] in neighbours:
            _set_mac(dh, neighbours[h["ip"]], f"arp:{source.split(':', 1)[-1]}")
        dh.hostname = h.get("hostname") or dh.hostname
        dh.up, dh.last_seen, dh.source = True, now, source
        db.session.add(dh)
        for s in h.get("services", []):
            key = (h["ip"], int(s["port"]), s.get("proto", "tcp"))
            seen_services.add(key)
            ds = db.session.scalar(db.select(DiscoveredService).where(
                DiscoveredService.ip == key[0], DiscoveredService.port == key[1], DiscoveredService.proto == key[2]))
            if ds is None:
                ds = DiscoveredService(ip=key[0], port=key[1], proto=key[2], first_seen=now)
            ds.state, ds.last_seen, ds.source = "open", now, source
            ds.service = s.get("service", "") or ds.service
            ds.product = s.get("product", "") or ds.product
            ds.version = s.get("version", "") or ds.version
            db.session.add(ds)
    up = {h["ip"] for h in hosts}
    closed = 0
    for ip, ports in (probed or {}).items():
        for port, proto in ports:
            key = (ip, int(port), proto or "tcp")
            if key in seen_services:
                continue
            ds = db.session.scalar(db.select(DiscoveredService).where(
                DiscoveredService.ip == key[0], DiscoveredService.port == key[1], DiscoveredService.proto == key[2]))
            if ds is None:
                ds = DiscoveredService(ip=key[0], port=key[1], proto=key[2], first_seen=now, state="closed")
            elif ds.state == "open":
                closed += 1
            ds.state, ds.last_seen, ds.source = "closed", now, source  # last_seen: when it was last checked
            db.session.add(ds)
    for ds in db.session.scalars(db.select(DiscoveredService).where(DiscoveredService.ip.in_(scanned))):
        if (ds.ip, ds.port, ds.proto) not in seen_services and ds.state == "open":
            ds.state = "closed"
            closed += 1
    for dh in db.session.scalars(db.select(DiscoveredHost).where(DiscoveredHost.ip.in_(scanned - up))):
        dh.up = False
    db.session.flush()
    return {"hosts_up": len(up), "open_ports": len(seen_services), "closed": closed}


def record_neighbours(source: str, entries: list[dict], mark_up: bool = True) -> int:
    """ARP/NDP/DHCP style data: [{ip, mac, hostname, vendor}]. mark_up=False for
    sources that only say an address exists (DNS), not that it answered."""
    now = utcnow()
    import ipaddress
    recorded = 0
    for e in entries:
        if not e.get("ip"):
            continue
        try:
            a = ipaddress.ip_address(e["ip"])
        except ValueError:
            continue
        if a.is_link_local or a.is_multicast or a.is_unspecified or a.is_loopback:
            continue  # fe80::, ff02::, … say nothing about the network
        dh = db.session.scalar(db.select(DiscoveredHost).where(DiscoveredHost.ip == e["ip"])) or DiscoveredHost(ip=e["ip"], first_seen=now)
        _set_mac(dh, e.get("mac") or "", source)
        dh.hostname = e.get("hostname") or dh.hostname
        dh.vendor = e.get("vendor") or dh.vendor
        if mark_up or dh.id is None:
            dh.last_seen = now
        if mark_up:
            dh.up = True
        elif dh.id is None:
            dh.up = False  # only known from a record, nobody saw it answer
        dh.source = dh.source if dh.source and dh.source != source else source
        db.session.add(dh)
        recorded += 1
    db.session.flush()
    return recorded


def _ip_index() -> dict[str, tuple[Host, Interface]]:
    out = {}
    for iface in db.session.scalars(db.select(Interface)):
        for ip in iface.all_addresses:
            if ip:
                out[ip] = (iface.host, iface)
    return out


def _host_facts(host: Host) -> rbac.Facts:
    return rbac.Facts.make(host.name, [host.zone], [i.network.name for i in host.interfaces if i.network],
                           host.tags or [], host.fun_names)


class _Net:
    """Light stand-in for a Network row (cached between requests)."""

    def __init__(self, name, zone):
        self.name, self.zone = name, zone


def _net_index():
    import ipaddress
    from .cache import memo
    from .models import Network

    def load():
        out = []
        for n in db.session.scalars(db.select(Network)):
            for p in n.prefixes:
                if p.prefix:
                    out.append((ipaddress.ip_network(p.prefix, strict=False), _Net(n.name, n.zone)))
        return sorted(out, key=lambda x: -x[0].prefixlen)  # most specific first
    return memo("discovery.nets", load)


def network_of(ip: str):
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for prefix, n in _net_index():
        if addr.version == prefix.version and addr in prefix:
            return n
    return None

_network_of = network_of  # the name before 1.3, for plugins written against it


def services(include_closed=False) -> list[dict]:
    idx = _ip_index()
    p = rbac.current()
    macs = {d.ip: d for d in db.session.scalars(db.select(DiscoveredHost).where(DiscoveredHost.mac != ""))}
    q = db.select(DiscoveredService)
    if not include_closed:
        q = q.where(DiscoveredService.state == "open")
    out = []
    for ds in db.session.scalars(q):
        host, iface = idx.get(ds.ip, (None, None))
        net = iface.network if iface is not None and iface.network else network_of(ds.ip)
        facts = _host_facts(host) if host else rbac.Facts.make(ds.ip, [net.zone] if net else [], [net.name] if net else [])
        if not p.can("discovery", "view", facts):
            continue
        declared = None
        if host is not None:
            declared = next((s for s in host.services if s.port == ds.port and (s.proto or "tcp") == ds.proto), None)
        status = "unknown-host" if host is None else ("documented" if declared else "undocumented")
        out.append({
            "id": ds.id, "ip": ds.ip, "port": ds.port, "proto": ds.proto, "state": ds.state,
            "service": ds.service, "product": ds.product, "version": ds.version, "source": ds.source,
            "first_seen": ds.first_seen.isoformat() + "Z", "last_seen": ds.last_seen.isoformat() + "Z",
            "status": status, "host": host.name if host else None, "fun": host.fun if host else None,
            "zone": host.zone if host else (net.zone if net else None),
            "network": net.name if net else None, "declared_as": declared.name if declared else None,
            "mac": macs[ds.ip].mac if ds.ip in macs else "", "vendor": macs[ds.ip].vendor if ds.ip in macs else "",
        })
    return sorted(out, key=lambda r: (_ipkey(r["ip"]), r["port"]))


def _ipkey(ip: str):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
        return (a.version, int(a))
    except ValueError:
        return (9, 0)


def missing(days: int = 7) -> list[dict]:
    """Declared services a recent scan checked on their addresses and found closed (never open)."""
    since = utcnow() - timedelta(days=days)
    states = {(d.ip, d.port, d.proto): d.state for d in db.session.scalars(db.select(DiscoveredService).where(
        (DiscoveredService.state == "open") | (DiscoveredService.last_seen >= since)))}
    p = rbac.current()
    out = []
    for host in db.session.scalars(db.select(Host).order_by(Host.name)):
        if not p.can("discovery", "view", _host_facts(host)):
            continue
        from .store import service_addresses
        for s in host.services:
            if not s.port:
                continue
            proto = s.proto or "tcp"
            ips = [ip for ip in service_addresses(host, s) if (ip, s.port, proto) in states]  # only where it's meant to listen
            if ips and not any(states[(ip, s.port, proto)] == "open" for ip in ips):
                out.append({"host": host.name, "fun": host.fun, "service": s.name, "port": s.port,
                            "proto": s.proto or "tcp", "ips": ips})
    return out


def unknown_hosts() -> list[dict]:
    """Addresses seen on the network that aren't in the inventory."""
    idx = _ip_index()
    p = rbac.current()
    out = []
    for dh in db.session.scalars(db.select(DiscoveredHost).order_by(DiscoveredHost.ip)):
        if dh.ip in idx:
            continue
        net = network_of(dh.ip)
        facts = rbac.Facts.make(dh.ip, [net.zone] if net else [], [net.name] if net else [])
        if not p.can("discovery", "view", facts):
            continue
        out.append({"ip": dh.ip, "mac": dh.mac, "mac_source": dh.mac_source, "hostname": dh.hostname, "vendor": dh.vendor, "up": dh.up,
                    "source": dh.source, "network": net.name if net else None,
                    "zone": net.zone if net else None, "last_seen": dh.last_seen.isoformat() + "Z"})
    return sorted(out, key=lambda r: _ipkey(r["ip"]))


def summary() -> dict:
    s = services()
    return {
        "open": len(s),
        "documented": sum(1 for x in s if x["status"] == "documented"),
        "undocumented": sum(1 for x in s if x["status"] == "undocumented"),
        "unknown_host": sum(1 for x in s if x["status"] == "unknown-host"),
        "unknown_hosts": len(unknown_hosts()),
        "missing": len(missing()),
        "macs": len([m for m in mac_suggestions() if rbac.current().can("host", "view", _host_facts(store_host(m["host"])))]),
        "domain": naming.get_setting("domain"),
    }


def live_status() -> dict[str, str]:
    """host name → up | down | unknown, from the newest discovery data about its addresses."""
    minutes = int(naming.get_setting("live_status_minutes") or 1440)
    since = utcnow() - timedelta(minutes=minutes)
    seen = {}
    for dh in db.session.scalars(db.select(DiscoveredHost)):
        seen[dh.ip] = "up" if dh.up and dh.last_seen >= since else "down"
    for (ip,) in db.session.execute(db.select(DiscoveredService.ip).where(
            DiscoveredService.state == "open", DiscoveredService.last_seen >= since).distinct()):
        seen[ip] = "up"
    out = {}
    for iface in db.session.scalars(db.select(Interface)):
        for ip in iface.all_addresses:
            st = seen.get(ip) if ip else None
            if st is None:
                continue
            name = iface.host.name
            if out.get(name) != "up":
                out[name] = st
    return out


def store_host(name: str) -> Host | None:
    return db.session.scalar(db.select(Host).where(Host.name == name))


def mac_suggestions() -> list[dict]:
    """Interfaces in the inventory without a MAC whose address Discovery has seen with one."""
    known = {d.ip: d for d in db.session.scalars(db.select(DiscoveredHost).where(DiscoveredHost.mac != ""))}
    out = []
    for iface in db.session.scalars(db.select(Interface).where(Interface.mac == "")):
        if iface.host.is_vip:
            continue  # a VIP's address answers with whichever member holds it
        for ip in iface.all_addresses:
            d = known.get(ip) if ip else None
            if d is not None:
                out.append({"host": iface.host.name, "fun": iface.host.fun or "", "zone": iface.host.zone,
                            "network": iface.network.name if iface.network else "", "label": iface.label, "ip": ip,
                            "mac": d.mac, "vendor": d.vendor, "source": d.mac_source or d.source,
                            "seen": d.last_seen.isoformat() + "Z" if d.last_seen else None})
                break
    return sorted(out, key=lambda x: (x["host"], x["ip"]))


def record_links(source: str, links: list[dict]) -> dict:
    """Links a switch (or controller) sees: LLDP/CDP neighbours, UniFi port tables. See cabling.record."""
    from . import cabling
    return cabling.record(source, links)


def record_macs(source: str, entries: list[dict]) -> dict:
    """MAC addresses a switch (or controller) has on its ports. See macs.record."""
    from . import macs
    return macs.record(source, entries)
