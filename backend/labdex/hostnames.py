"""Hostname building blocks: <category>-<type>-<role>-<nn>.

Categories, types and roles are lists you maintain (Settings → Naming). The host
form builds the name from three dropdowns and takes the next free number for
that combination. Names outside the scheme are still allowed (legacy hosts);
the linter points them out.
"""
from __future__ import annotations

import re

from . import naming, store
from .models import Host, NamingPart, db

KINDS = ("category", "type", "role")
CODE_RE = re.compile(r"^[a-z0-9]{1,16}$")

STARTER = {
    "category": [("net", "Network gear"), ("srv", "Servers"), ("dmz", "Internet-facing"), ("sto", "Storage"),
                 ("lab", "Lab"), ("pwr", "Power"), ("cli", "Clients"), ("ext", "External")],
    "type": [("pm", "Physical machine"), ("vm", "Virtual machine"), ("ct", "Container / LXC"), ("fw", "Firewall"),
             ("rt", "Router"), ("sw", "Switch"), ("ap", "Access point"), ("nas", "NAS"), ("ups", "UPS"),
             ("bmc", "BMC / iDRAC"), ("nb", "Notebook"), ("pc", "Desktop"), ("vps", "VPS"), ("vip", "Virtual IP (VIP)")],
    "role": [("edge", "Edge"), ("core", "Core"), ("dns", "DNS"), ("mon", "Monitoring"), ("web", "Web"),
             ("db", "Database"), ("pve", "Proxmox"), ("k3s", "Kubernetes node"), ("admin", "Admin"), ("backup", "Backup")],
}


def parts() -> dict[str, list[dict]]:
    out = {k: [] for k in KINDS}
    for p in db.session.scalars(db.select(NamingPart).order_by(NamingPart.kind, NamingPart.position, NamingPart.code)):
        if p.kind in out:
            out[p.kind].append(part_dict(p))
    return out


def part_dict(p: NamingPart) -> dict:
    return {"kind": p.kind, "code": p.code, "label": p.label, "description": p.description, "position": p.position}


def ensure_seeded() -> None:
    """First start: take categories from the old setting and types/roles from existing
    hostnames; on an empty inventory, a starter set you can edit."""
    if db.session.scalar(db.select(db.func.count()).select_from(NamingPart)):
        return
    hosts = [h.name for h in db.session.scalars(db.select(Host))]
    cats = [c.strip() for c in naming.get_setting("categories").split(",") if c.strip()]
    if hosts:
        found = {k: [] for k in KINDS}
        rx = re.compile(r"^([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-\d{2}$")
        for n in hosts:
            m = rx.match(n)
            if m and m.group(1) in cats:
                for k, v in zip(KINDS, m.groups()):
                    if v not in found[k]:
                        found[k].append(v)
        labels = {k: dict(v) for k, v in STARTER.items()}
        seed = {"category": [(c, labels["category"].get(c, c)) for c in cats],
                "type": [(t, labels["type"].get(t, t)) for t in sorted(set(found["type"]) | {"vip"})],
                "role": [(r, labels["role"].get(r, r)) for r in sorted(found["role"])]}
    else:
        seed = STARTER
    for kind, items in seed.items():
        for i, (code, label) in enumerate(items):
            db.session.add(NamingPart(kind=kind, code=code, label=label, position=i))
    db.session.flush()


def save(kind: str, data: dict, existing: NamingPart | None = None) -> NamingPart:
    if kind not in KINDS:
        raise store.ValidationError([{"field": "kind", "message": "category, type or role"}])
    code = str(data.get("code", "")).strip().lower()
    errors = []
    if not CODE_RE.match(code):
        errors.append({"field": "code", "message": "1–16 lowercase letters or digits"})
    clash = db.session.scalar(db.select(NamingPart).where(NamingPart.kind == kind, NamingPart.code == code))
    if clash is not None and clash is not existing:
        errors.append({"field": "code", "message": "already exists"})
    if existing is not None and code != existing.code and used(kind, existing.code):
        errors.append({"field": "code", "message": f"hosts use '{existing.code}'; rename them first (the label can change any time)"})
    if errors:
        raise store.ValidationError(errors)
    p = existing or NamingPart(kind=kind, position=(db.session.scalar(db.select(db.func.max(NamingPart.position)).where(NamingPart.kind == kind)) or 0) + 1)
    p.code, p.label, p.description = code, str(data.get("label") or code)[:64], str(data.get("description", ""))
    if data.get("position") is not None:
        p.position = int(data["position"])
    db.session.add(p)
    return p


def _split(name: str):
    m = re.match(r"^([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-(\d{2,3})$", name or "")
    return m.groups() if m else None


def used(kind: str, code: str) -> list[str]:
    idx = KINDS.index(kind)
    return [h.name for h in db.session.scalars(db.select(Host)) if (s := _split(h.name)) and s[idx] == code]


def delete(kind: str, code: str) -> None:
    p = db.session.scalar(db.select(NamingPart).where(NamingPart.kind == kind, NamingPart.code == code))
    if p is None:
        raise store.ValidationError([{"field": "code", "message": "not found"}])
    hosts = used(kind, code)
    if hosts:
        raise store.ValidationError([{"field": "code", "message": f"used by {', '.join(hosts[:6])}{'…' if len(hosts) > 6 else ''}"}])
    db.session.delete(p)


def next_name(category: str, type_: str, role: str, exclude: str | None = None) -> dict:
    prefix = f"{category}-{type_}-{role}-"
    taken = set()
    for (n,) in db.session.execute(db.select(Host.name).where(Host.name.like(prefix + "%"))):
        if n != exclude and (s := _split(n)):
            taken.add(int(s[3]))
    nn = next(i for i in range(1, 1000) if i not in taken)
    p = parts()
    known = {k: {x["code"] for x in p[k]} for k in KINDS}
    # roles are free text: only an unknown category or type is worth a warning
    unknown = [f"{k} '{v}'" for k, v in zip(KINDS, (category, type_, role)) if k != "role" and v not in known[k]]
    return {"name": f"{prefix}{nn:02d}", "nn": nn, "taken": sorted(taken), "unknown": unknown}
