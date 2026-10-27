"""Racks and where hosts sit in them."""
from __future__ import annotations

import re
from typing import NamedTuple

from . import store
from .models import RACK_FACES, Host, Rack, RackItem, db

# what sits in a rack that isn't a host
ITEM_KINDS = {"patch": "patch panel", "cables": "cable management", "blank": "blank panel", "shelf": "shelf", "pdu": "PDU",
              "other": "other"}

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


def find(key: str) -> Rack | None:
    """By name or stable id."""
    key = str(key or "").strip()
    return db.session.scalar(db.select(Rack).where(Rack.name == key)) or db.session.scalar(db.select(Rack).where(Rack.uid == key.lower()))


def item_dict(i: RackItem) -> dict:
    return {"id": i.id, "kind": i.kind, "label": i.label or "", "description": i.description or "", "unit": i.unit, "height": i.height,
            "face": i.face}


class Clash(NamedTuple):
    unit: int
    label: str


def clashes(rack: Rack, unit: int, height: int, face: str, skip_item: int | None = None, skip_host: int | None = None) -> Clash | None:
    """What already takes one of these units on that side (a host or an item), or None. Front and
    rear share a unit; full depth takes both."""
    mine = set(range(unit, unit + height))

    def same_side(other: str) -> bool:
        sides = {other or "front", face}
        return "full" in sides or len(sides) == 1

    for o in db.session.scalars(db.select(Host).where(Host.rack_id == rack.id, Host.rack_unit.is_not(None))):
        if o.id != skip_host and same_side(o.rack_face) and mine & set(range(o.rack_unit, o.rack_unit + (o.rack_height or 1))):
            return Clash(o.rack_unit, o.name)
    for i in db.session.scalars(db.select(RackItem).where(RackItem.rack_id == rack.id)):
        if i.id != skip_item and same_side(i.face) and mine & set(range(i.unit, i.unit + i.height)):
            return Clash(i.unit, i.label or ITEM_KINDS.get(i.kind, i.kind))
    return None


def save_item(rack: Rack, data: dict, existing: RackItem | None = None) -> RackItem:
    errors = []
    kind = str(data.get("kind") or "blank")
    if kind not in ITEM_KINDS:
        errors.append({"field": "kind", "message": f"one of {', '.join(ITEM_KINDS)}"})
    face = str(data.get("face") or "front")
    if face not in RACK_FACES:
        errors.append({"field": "face", "message": f"one of {', '.join(RACK_FACES)}"})
    try:
        unit, height = int(data.get("unit")), int(data.get("height") or 1)
        if unit < 1 or height < 1 or unit + height - 1 > rack.units:
            raise ValueError
    except (TypeError, ValueError):
        errors.append({"field": "unit", "message": f"inside {rack.name}: units 1 to {rack.units}"})
        unit, height = 1, 1
    if not errors:
        c = clashes(rack, unit, height, face, skip_item=existing.id if existing else None)
        if c is not None:
            errors.append({"field": "unit", "message": f"U{c.unit} holds {c.label}"})
    if errors:
        raise store.ValidationError(errors)
    i = existing or RackItem(rack_id=rack.id)
    i.kind, i.face, i.unit, i.height = kind, face, unit, height
    i.label = str(data.get("label") or "").strip()[:64]
    i.description = str(data.get("description") or "").strip()[:500]
    if existing is None:
        db.session.add(i)
    db.session.flush()
    return i


def to_dict(r: Rack, with_hosts=False, can_see=None) -> dict:
    d = {"uid": r.uid, "name": r.name, "site": r.site or "", "units": r.units, "description": r.description or "", "position": r.position}
    if with_hosts:
        hosts = db.session.scalars(db.select(Host).where(Host.rack_id == r.id).order_by(Host.rack_unit.desc())).all()
        d["hosts"] = [{"name": h.name, "uid": h.uid, "fun": h.fun or "", "status": h.status, "zone": h.zone, "function": h.function,
                       "icon": h.icon or "", "kind": h.kind, "unit": h.rack_unit, "height": h.rack_height or 1, "face": h.rack_face or "front",
                       "hardware": h.hardware or ""}
                      for h in hosts if can_see is None or can_see(h)]
        items = db.session.scalars(db.select(RackItem).where(RackItem.rack_id == r.id).order_by(RackItem.unit.desc())).all()
        d["items"] = [item_dict(i) for i in items]
        d["used"] = sum((h.rack_height or 1) for h in hosts if h.rack_unit) + sum(i.height for i in items)
    return d


def save(data: dict, existing: Rack | None = None) -> Rack:
    errors = []
    name = str(data.get("name", "")).strip()
    if not NAME_RE.match(name):
        errors.append({"field": "name", "message": "letters, digits, spaces and . _ - (at most 64)"})
    other = db.session.scalar(db.select(Rack).where(Rack.name == name))
    if other is not None and other is not existing:
        errors.append({"field": "name", "message": f"rack '{name}' already exists"})
    try:
        units = int(data.get("units") or 42)
        if not 1 <= units <= 60:
            raise ValueError
    except (TypeError, ValueError):
        errors.append({"field": "units", "message": "1 to 60"})
        units = 42
    if existing is not None:
        top = max(db.session.scalar(db.select(db.func.max(Host.rack_unit + Host.rack_height - 1)).where(Host.rack_id == existing.id)) or 0,
                  db.session.scalar(db.select(db.func.max(RackItem.unit + RackItem.height - 1)).where(RackItem.rack_id == existing.id)) or 0)
        if top > units:
            errors.append({"field": "units", "message": f"something reaches unit {top}; move it first"})
    if errors:
        raise store.ValidationError(errors)
    r = existing or Rack(position=(db.session.scalar(db.select(db.func.max(Rack.position))) or 0) + 1)
    r.name, r.units = name, units
    r.site = str(data.get("site") or "").strip()[:64]
    r.description = str(data.get("description") or "")[:2000]
    if existing is None:
        db.session.add(r)
    db.session.flush()
    if "items" in data and existing is None:  # from an import: what fills the rack besides hosts
        for n, it in enumerate(data.get("items") or []):
            try:
                save_item(r, it)
            except store.ValidationError as exc:
                raise store.ValidationError([{**x, "field": f"items[{n}].{x['field']}"} for x in exc.errors]) from exc
    return r


def place(data: dict, existing: Host | None, e) -> dict | None:
    """A host's rack fields from its form: {rack, rack_unit, rack_height, rack_face}. None = leave as is
    (the form didn't send them); {"rack": None} takes it out of its rack."""
    if "rack" not in data and "rack_height" not in data:
        return None
    # the size (in units, "HE") is the host's own: it stays when the host leaves a rack, and placing
    # it uses it
    try:
        size = int(data.get("rack_height") or (existing.rack_height if existing is not None else 1) or 1)
        if not 1 <= size <= 60:
            raise ValueError
    except (TypeError, ValueError):
        e.add("rack_height", "1 to 60 units")
        return None
    key = str(data.get("rack") or "").strip()
    if not key:
        return {"rack": None, "rack_unit": None, "rack_height": size, "rack_face": "front"}
    rack = find(key)
    if rack is None:
        e.add("rack", f"no rack '{key}'")
        return None
    face = str(data.get("rack_face") or "front").lower()
    if face not in RACK_FACES:
        e.add("rack_face", f"one of {', '.join(RACK_FACES)}")
    height = size
    if height > rack.units:
        e.add("rack_height", f"{rack.name} has {rack.units} units")
        return None
    raw = data.get("rack_unit")
    if raw in (None, ""):
        return {"rack": rack, "rack_unit": None, "rack_height": height, "rack_face": face}  # in the rack, no unit yet
    try:
        unit = int(raw)
        if not 1 <= unit or unit + height - 1 > rack.units:
            raise ValueError
    except (TypeError, ValueError):
        e.add("rack_unit", f"1 to {rack.units - height + 1} for a {height} U device in {rack.name}")
        return None
    c = clashes(rack, unit, height, face, skip_host=existing.id if existing is not None else None)
    if c is not None:
        e.add("rack_unit", f"{rack.name} U{c.unit} holds {c.label}")
    return {"rack": rack, "rack_unit": unit, "rack_height": height, "rack_face": face}
