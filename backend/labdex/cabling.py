"""Cables, the parts shared by everything that makes one, and the ones switches tell us about.

Sources (UniFi, the SNMP plugin reading LLDP-MIB and CISCO-CDP-MIB) report the links they see
with `record(source, links)`; each report replaces that source's last one. A link:

    {"host": "net-sw-core-01"                     # the reporting end, if the source knows the host
     "device": {"name", "ip", "mac"},             # … or how it calls itself
     "port": "ge-0/0/1",
     "remote": {"name", "ip", "mac", "platform"},  # the neighbour, as seen
     "remote_port": "eno1", "protocol": "lldp"}

Ends are matched to inventory hosts by name (or fun name, or any DNS name labdex makes), by an
interface MAC, or by an address. `suggestions()` compares what's seen with the cables on
record: new ones, ones that contradict a cable, and neighbours labdex doesn't know.
"""
from __future__ import annotations

from flask import g, has_request_context

from . import audit, rbac, store
from .models import CableIgnore, Host, Link, LinkSeen, db, utcnow


# --------------------------------------------------------------------------- matching
def _matcher():
    from . import health
    by_text = health.host_matcher()
    by_mac: dict[str, Host] = {}
    hosts = {h.name: h for h in db.session.scalars(db.select(Host))}
    for h in hosts.values():
        for i in h.interfaces:
            if i.mac:
                by_mac.setdefault(i.mac.lower(), h)

    def match(name: str = "", ip: str = "", mac: str = "") -> Host | None:
        if mac and mac.lower() in by_mac:
            return by_mac[mac.lower()]
        for t in (name, ip):
            n = by_text(t) if t else None
            if n and n in hosts:
                return hosts[n]
        return None
    return match


def _port(p) -> str:
    return str(p or "").strip()[:64]


def record(source: str, links: list[dict]) -> dict:
    """What `source` sees now. Returns how many links it reported and how many ends matched hosts."""
    match = _matcher()
    for old in db.session.scalars(db.select(LinkSeen).where(LinkSeen.source == source)):
        db.session.delete(old)
    db.session.flush()
    now, n, matched = utcnow(), 0, 0
    seen = set()
    for lk in links or []:
        dev, rem = lk.get("device") or {}, lk.get("remote") or {}
        port, rport = _port(lk.get("port")), _port(lk.get("remote_port"))
        if not port:
            continue
        a = store.find_host(str(lk.get("host") or "")) if lk.get("host") else match(dev.get("name", ""), dev.get("ip", ""), dev.get("mac", ""))
        b = match(rem.get("name", ""), rem.get("ip", ""), rem.get("mac", ""))
        rmac = str(rem.get("mac") or "").lower()
        if not rport and b is not None and rmac:  # a client knows no port name: its interface with that MAC
            rport = next((i.label for i in b.interfaces if (i.mac or "").lower() == rmac and i.label), "")
        key = (a.id if a else dev.get("name"), port, b.id if b else (rmac or rem.get("name")), rport)
        if key in seen:
            continue
        seen.add(key)
        db.session.add(LinkSeen(source=source[:120], protocol=str(lk.get("protocol") or "")[:16], a_host=a, a_name=str(dev.get("name") or (a.name if a else ""))[:255],
                                a_port=port, b_host=b, b_name=str(rem.get("name") or "")[:255], b_port=rport, b_mac=str(rem.get("mac") or "").lower()[:17],
                                b_ip=str(rem.get("ip") or "")[:45], b_platform=str(rem.get("platform") or "")[:120], seen_at=now))
        n += 1
        matched += bool(a) + bool(b)
    db.session.flush()
    return {"links": n, "matched_ends": matched}


# ------------------------------------------------------------------------ suggestions
def key(a_host: str, a_port: str, b_host: str, b_port: str) -> str:
    ends = sorted([f"{a_host}:{a_port.lower()}", f"{b_host}:{b_port.lower()}"])
    return "|".join(ends)


def _cable_at(cables: dict, host_id: int, port: str):
    return cables.get((host_id, port.lower()))


def suggestions(visible=None) -> dict:
    """{"suggested": [...], "conflicts": [...], "documented": n, "unknown": [...], "ignored": n}.
    visible: host names the caller may see (None: all)."""
    cables: dict[tuple[int, str], Link] = {}
    for lk in db.session.scalars(db.select(Link).where(Link.medium != "virtual")):
        cables[(lk.a_host_id, lk.a_port.lower())] = lk
        cables[(lk.b_host_id, lk.b_port.lower())] = lk
    ignored = {x.key for x in db.session.scalars(db.select(CableIgnore))}
    out = {"suggested": [], "conflicts": [], "documented": 0, "unknown": [], "ignored": 0}
    done = set()
    ok = (lambda h: h is not None and (visible is None or h.name in visible))
    for s in db.session.scalars(db.select(LinkSeen).order_by(LinkSeen.id)):
        a, b = s.a_host, s.b_host
        if not ok(a):
            continue
        if b is None:
            if s.b_name or s.b_mac or s.b_ip:
                out["unknown"].append({"host": a.name, "port": s.a_port, "name": s.b_name, "mac": s.b_mac, "ip": s.b_ip,
                                       "remote_port": s.b_port, "platform": s.b_platform, "source": s.source, "protocol": s.protocol})
            continue
        if not ok(b) or not (s.b_port or s.b_mac):
            continue
        k = key(a.name, s.a_port, b.name, s.b_port or s.b_mac)
        if k in done:  # both ends reported it
            continue
        done.add(k)
        here = _cable_at(cables, a.id, s.a_port)
        there = _cable_at(cables, b.id, s.b_port) if s.b_port else None
        item = {"key": k, "a_host": a.name, "a_uid": a.uid, "a_port": s.a_port, "b_host": b.name, "b_uid": b.uid, "b_port": s.b_port, "b_mac": s.b_mac,
                "protocol": s.protocol, "source": s.source, "seen_at": s.seen_at.isoformat() + "Z" if s.seen_at else None}
        if here is not None and (here is there or (not s.b_port and (here.b_host_id if here.a_host_id == a.id else here.a_host_id) == b.id)):
            out["documented"] += 1
            continue
        if k in ignored:
            out["ignored"] += 1
            continue
        if here is not None or there is not None:
            def describe(lk, host_id):
                mine_a = lk.a_host_id == host_id
                other = lk.b_host if mine_a else lk.a_host
                return f"{other.name} {lk.b_port if mine_a else lk.a_port}"
            item["now"] = {"a": describe(here, a.id) if here else "", "b": describe(there, b.id) if there else ""}
            out["conflicts"].append(item)
        else:
            out["suggested"].append(item)
    return out


def ignore(keys: list[str], actor: str) -> int:
    n = 0
    for k in keys:
        k = str(k)[:400]
        if k and db.session.get(CableIgnore, k) is None:
            db.session.add(CableIgnore(key=k, by=actor))
            n += 1
    db.session.flush()
    return n


# ------------------------------------------------------------------------ making one
def _with_port(d: dict, port: str, side: dict) -> list:
    """The interface the cable plugs into: one with that label (any case), an unnamed one the form
    picked (by its position) that gets the name, or a new one with only the name."""
    ifaces = [dict(i) for i in d["interfaces"]]
    if any((i.get("label") or "").lower() == port.lower() for i in ifaces):
        return ifaces
    idx = side.get("iface")
    if isinstance(idx, int) and 0 <= idx < len(ifaces) and not ifaces[idx].get("label"):
        ifaces[idx]["label"] = port
        return ifaces
    return [*ifaces, {"label": port, "network": "", "ip4": "", "ip6": ""}]


def _label(d: dict, port: str) -> str:
    """The port's name as the host has it (ge-0/0/1 for GE-0/0/1)."""
    return next((i["label"] for i in d["interfaces"] if (i.get("label") or "").lower() == port.lower()), port)


def add(ha: Host, pa: str, hb: Host, pb: str, extra: dict | None = None, a_side: dict | None = None, b_side: dict | None = None,
        replace: bool = False) -> str:
    """A cable between two ports, through the normal host validation (one history entry per host).
    Ports a host doesn't have yet become interfaces. replace: a cable already on either port goes.
    Needs host:edit on both ends. Raises store.ValidationError."""
    extra, a_side, b_side = extra or {}, a_side or {}, b_side or {}
    befores = {}
    for h in dict.fromkeys((ha, hb)):
        befores[h.name] = store.host_to_dict(h)
        rbac.require("host", "edit", rbac.host_facts(befores[h.name]))
    pa, pb = _label(befores[ha.name], pa), _label(befores[hb.name], pb)
    cable = {"port": pa, "peer": hb.name, "peer_port": pb, "medium": str(extra.get("medium") or "copper").strip().lower(),
             "speed": str(extra.get("speed") or "").strip()[:16], "notes": str(extra.get("notes") or "").strip()[:200],
             **{k: str(extra.get(k) or "").strip() for k in ("label", "length", "colour")}}

    def keep(d, ports):  # replace: the cables on these ports (of this host) go
        return [c for c in d["cables"] if not (c["medium"] != "virtual" and c["port"].lower() in ports)] if replace else d["cables"]

    summary = f"cable {ha.name} {pa} – {hb.name} {pb}"
    if ha is hb:  # inside one host: both ports, one save
        d = befores[ha.name]
        d = {**d, "interfaces": _with_port(d, pb, b_side)}
        store.save_host({**d, "interfaces": _with_port(d, pa, a_side), "cables": [*keep(d, {pa.lower(), pb.lower()}), cable]}, existing=ha)
    else:
        db_b = befores[hb.name]
        store.save_host({**db_b, "interfaces": _with_port(db_b, pb, b_side), "cables": keep(db_b, {pb.lower()})}, existing=hb)
        db.session.flush()
        db_a = store.host_to_dict(ha)  # again: the far end's save may have taken a cable of this one
        store.save_host({**db_a, "interfaces": _with_port(db_a, pa, a_side), "cables": [*keep(db_a, {pa.lower()}), cable]}, existing=ha)
    db.session.flush()
    for h in dict.fromkeys((ha, hb)):
        audit.record("update", "host", h.name, befores[h.name], store.host_to_dict(h), summary=summary)
    return summary


def actor() -> str:
    return g.principal.label if has_request_context() and getattr(g, "principal", None) else "labdex"


# ------------------------------------------------------------------ trace
MAX_HOPS = 32


def _cable_on(host_id: int, port: str) -> Link | None:
    p = port.lower()
    for lk in db.session.scalars(db.select(Link).where(db.or_(Link.a_host_id == host_id, Link.b_host_id == host_id))):
        if lk.medium == "virtual":
            continue
        if lk.a_host_id == host_id and lk.a_port.lower() == p or lk.b_host_id == host_id and lk.b_port.lower() == p:
            return lk
    return None


def _partner(host: Host, port: str) -> str:
    p = port.lower()
    i = next((i for i in host.interfaces if (i.label or "").lower() == p), None)
    return (i.passthrough or "") if i is not None else ""


def _cable_dict(lk: Link) -> dict:
    return {"id": lk.id, "label": lk.label or "", "medium": lk.medium or "", "speed": lk.speed or "", "length": lk.length or "",
            "colour": lk.colour or "", "notes": lk.notes or ""}


def _walk(host: Host, port: str, seen: set) -> list[dict]:
    """From a port outwards: its cable, the far port, through the far host if that port passes
    through, and on, until a port with no cable or no pass-through."""
    out = []
    while len(out) < MAX_HOPS:
        lk = _cable_on(host.id, port)
        if lk is None:
            break
        if lk.a_host_id == host.id and lk.a_port.lower() == port.lower():
            far, far_port = lk.b_host, lk.b_port
        else:
            far, far_port = lk.a_host, lk.a_port
        out.append({"type": "cable", "cable": _cable_dict(lk), "to": {"host": far.name, "uid": far.uid or "", "port": far_port}})
        key = (far.id, far_port.lower())
        if key in seen:
            out.append({"type": "loop"})
            break
        seen.add(key)
        through = _partner(far, far_port)
        if not through:
            break
        out.append({"type": "through", "host": far.name, "uid": far.uid or "", "from": far_port, "to": through})
        host, port = far, through
        seen.add((far.id, through.lower()))
    return out


def trace(host: Host, port: str) -> dict:
    """A port's whole path: both directions if the port itself passes through (a patch panel
    port in the middle of a run), as one list of points from one end to the other.

    Each point is {"host", "uid", "port"}; between two points either a cable (its label,
    colour, length …) or "through" (inside a patch panel, front to rear)."""
    port = _label(store.host_to_dict(host), port)
    seen = {(host.id, port.lower())}
    start = {"host": host.name, "uid": host.uid or "", "port": port}
    fwd = _walk(host, port, seen)
    back = []
    through = _partner(host, port)
    if through:
        seen.add((host.id, through.lower()))
        back = _walk(host, through, seen)
    # one list from the far end of "back" to the far end of "fwd"
    points, links = [start], []
    for step in fwd:
        if step["type"] == "cable":
            links.append({"cable": step["cable"]})
            points.append(step["to"])
        elif step["type"] == "through":
            links.append({"through": True})
            points.append({"host": step["host"], "uid": step["uid"], "port": step["to"]})
    if through:
        bpoints, blinks = [{"host": host.name, "uid": host.uid or "", "port": through}], [{"through": True}]
        for step in back:
            if step["type"] == "cable":
                blinks.append({"cable": step["cable"]})
                bpoints.append(step["to"])
            elif step["type"] == "through":
                blinks.append({"through": True})
                bpoints.append({"host": step["host"], "uid": step["uid"], "port": step["to"]})
        points = list(reversed(bpoints)) + points
        links = list(reversed(blinks)) + links
    loop = any(s["type"] == "loop" for s in fwd + back)
    cables = sum(1 for lk in links if "cable" in lk)
    return {"points": points, "links": links, "cables": cables, "loop": loop,
            "complete": cables > 0 and not loop, "ends": [points[0], points[-1]]}
