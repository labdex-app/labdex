"""The zone policy matrix: which zone may reach which, on which ports.

Deny unless listed: a missing cell means no access. A cell lists "any", port
aliases from the inventory (type port, e.g. SG_WEB) or literal ports like tcp/443,
udp/53 or tcp/8000-8100. Services' "allow from" adds narrower access on top.
"""
from __future__ import annotations

import re

from . import naming
from .models import AliasGroup, ZonePolicy, db

PORT_RE = re.compile(r"^(tcp|udp)/(\d{1,5})(-(\d{1,5}))?$")


def cells() -> list[ZonePolicy]:
    return list(db.session.scalars(db.select(ZonePolicy).order_by(ZonePolicy.src, ZonePolicy.dst)))


def cell_dict(c: ZonePolicy) -> dict:
    return {"src": c.src, "dst": c.dst, "ports": list(c.ports or []), "description": c.description or ""}


def port_aliases() -> list[str]:
    return [g.name for g in db.session.scalars(db.select(AliasGroup).where(AliasGroup.type == "port").order_by(AliasGroup.name))]


def validate(src: str, dst: str, ports: list) -> list[dict]:
    errs = []
    zones = set(naming.all_zones())
    for k, z in (("src", src), ("dst", dst)):
        if z not in zones:
            errs.append({"field": k, "message": f"unknown zone '{z}'"})
    if not isinstance(ports, list):
        return errs + [{"field": "ports", "message": "a list"}]
    return errs + [{"field": "ports", "message": m} for m in validate_ports(ports)]


def validate_ports(ports: list) -> list[str]:
    """Messages for entries that aren't any, a port alias or tcp/443-style ports."""
    errs = []
    known = set(port_aliases())
    for p in ports:
        p = str(p)
        m = PORT_RE.match(p.lower())
        if p == "any" or p in known:
            continue
        if m and 1 <= int(m.group(2)) <= 65535 and (not m.group(4) or int(m.group(2)) <= int(m.group(4)) <= 65535):
            continue
        errs.append(f"'{p}': any, a port alias ({', '.join(sorted(known)) or 'none yet'}) or tcp/443, udp/53, tcp/8000-8100")
    if "any" in ports and len(ports) > 1:
        errs.append("'any' already covers every port")
    return errs


def put(src: str, dst: str, ports: list, description: str = "") -> ZonePolicy | None:
    """Set a cell; no ports removes it. Returns the cell (or None when removed)."""
    c = db.session.scalar(db.select(ZonePolicy).where(ZonePolicy.src == src, ZonePolicy.dst == dst))
    ports = [str(p).lower() if PORT_RE.match(str(p).lower()) else str(p) for p in ports]
    if not ports:
        if c is not None:
            db.session.delete(c)
        return None
    if c is None:
        c = ZonePolicy(src=src, dst=dst)
        db.session.add(c)
    c.ports, c.description = list(dict.fromkeys(ports)), (description or "")[:200]
    return c


def allows(src: str, dst: str, port: int | None, proto: str = "tcp") -> bool | None:
    """Does the matrix let src reach dst on this port? None = no cell."""
    c = db.session.scalar(db.select(ZonePolicy).where(ZonePolicy.src == src, ZonePolicy.dst == dst))
    if c is None:
        return None
    if "any" in c.ports:
        return True
    from .generators import aliases
    by_name = {a.name: a for a in aliases.all_aliases()}
    for p in c.ports:
        m = PORT_RE.match(p)
        if m:
            lo, hi = int(m.group(2)), int(m.group(4) or m.group(2))
            if m.group(1) == proto and port is not None and lo <= port <= hi:
                return True
        elif p in by_name and port is not None:
            for member in by_name[p].content:
                mm = re.match(r"^(\d+)(?::(\d+))?$", str(member))
                if mm and int(mm.group(1)) <= port <= int(mm.group(2) or mm.group(1)):
                    return True
    return False
