"""Racks: their units and which hosts sit where. Everyone sees the racks; hosts by their rights.
People who edit hosts change racks and place hosts in them."""
from __future__ import annotations

from flask import g, jsonify

from .. import audit, racks, rbac, store
from ..models import Host, Rack, RackItem, db
from ..plugins import jobs
from . import body, bp, err


def _can_see(h: Host) -> bool:
    return g.principal.can("host", "view", rbac.host_facts(store.host_access(h)))


def _can_arrange() -> bool:
    return g.principal.can_any("host", "edit")


def _out(r: Rack) -> dict:
    return racks.to_dict(r, with_hosts=True, can_see=_can_see) | {"can_edit": _can_arrange()}


@bp.get("/racks")
def list_racks():
    rows = db.session.scalars(db.select(Rack).order_by(Rack.site, Rack.position, Rack.name))
    return jsonify({"racks": [_out(r) for r in rows], "can_edit": _can_arrange()})


@bp.get("/racks/<key>")
def get_rack(key):
    r = racks.find(key)
    if r is None:
        return err(404, f"no rack '{key}'")
    return jsonify(_out(r))


@bp.post("/racks")
@bp.put("/racks/<key>")
def save_rack(key=None):
    if not _can_arrange():
        return err(403, "racks are changed by people who edit hosts")
    existing = racks.find(key) if key else None
    if key and existing is None:
        return err(404, f"no rack '{key}'")
    before = racks.to_dict(existing) if existing else None
    r = racks.save(body(), existing)
    audit.record("update" if existing else "create", "rack", r.name, before, racks.to_dict(r))
    db.session.commit()
    jobs.emit("rack.updated")
    return jsonify(_out(r)), 200 if existing else 201


@bp.delete("/racks/<key>")
def delete_rack(key):
    if not _can_arrange():
        return err(403, "racks are changed by people who edit hosts")
    r = racks.find(key)
    if r is None:
        return err(404, f"no rack '{key}'")
    before = racks.to_dict(r)
    for h in db.session.scalars(db.select(Host).where(Host.rack_id == r.id)):
        hb = store.host_to_dict(h)
        h.rack, h.rack_unit = None, None
        db.session.flush()
        audit.record("update", "host", h.name, hb, store.host_to_dict(h), summary=f"out of rack {r.name} (deleted)")
    for i in db.session.scalars(db.select(RackItem).where(RackItem.rack_id == r.id)):
        db.session.delete(i)
    db.session.delete(r)
    audit.record("delete", "rack", before["name"], before, None)
    db.session.commit()
    jobs.emit("rack.deleted")
    return "", 204


@bp.put("/racks/<key>/hosts/<name>")
def place_host(key, name):
    """{unit, height, face}: put a host there (moving it from wherever it was); unit null = in the rack, not placed."""
    r = racks.find(key)
    h = store.find_host(name)
    if r is None or h is None or not _can_see(h):
        return err(404, "no such rack or host")
    before = store.host_to_dict(h)
    rbac.require("host", "edit", rbac.host_facts(before))
    d = body()
    store.save_host({**before, "rack": r.name, "rack_unit": d.get("unit"), "rack_height": d.get("height") or before["rack_height"],
                     "rack_face": d.get("face") or before["rack_face"]}, existing=h)
    db.session.flush()
    audit.record("update", "host", h.name, before, store.host_to_dict(h), summary=f"in rack {r.name} U{d.get('unit')}")
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify(_out(r))


@bp.delete("/racks/<key>/hosts/<name>")
def unplace_host(key, name):
    r = racks.find(key)
    h = store.find_host(name)
    if r is None or h is None or h.rack_id != r.id or not _can_see(h):
        return err(404, "no such host in this rack")
    before = store.host_to_dict(h)
    rbac.require("host", "edit", rbac.host_facts(before))
    store.save_host({**before, "rack": ""}, existing=h)
    db.session.flush()
    audit.record("update", "host", h.name, before, store.host_to_dict(h), summary=f"out of rack {r.name}")
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify(_out(r))


# ---------------------------------------------------- what else is in a rack
def _rack_for_items(key):
    if not _can_arrange():
        return None, err(403, "racks are changed by people who edit hosts")
    r = racks.find(key)
    if r is None:
        return None, err(404, f"no rack '{key}'")
    return r, None


@bp.post("/racks/<key>/items")
@bp.put("/racks/<key>/items/<int:item_id>")
def save_rack_item(key, item_id=None):
    """{kind: patch|cables|blank|shelf|pdu|other, label, description, unit, height, face}"""
    r, problem = _rack_for_items(key)
    if problem:
        return problem
    existing = None
    if item_id is not None:
        existing = db.session.get(RackItem, item_id)
        if existing is None or existing.rack_id != r.id:
            return err(404, "no such item in this rack")
    i = racks.save_item(r, body(), existing)
    what = f"{i.label or racks.ITEM_KINDS[i.kind]} at U{i.unit}"
    audit.record("update", "rack", r.name, None, racks.item_dict(i), summary=f"{'changed' if existing else 'added'} {what}")
    db.session.commit()
    jobs.emit("rack.updated")
    return jsonify(_out(r)), 200 if existing else 201


@bp.delete("/racks/<key>/items/<int:item_id>")
def delete_rack_item(key, item_id):
    r, problem = _rack_for_items(key)
    if problem:
        return problem
    i = db.session.get(RackItem, item_id)
    if i is None or i.rack_id != r.id:
        return err(404, "no such item in this rack")
    before = racks.item_dict(i)
    db.session.delete(i)
    audit.record("update", "rack", r.name, before, None, summary=f"removed {before['label'] or racks.ITEM_KINDS[before['kind']]} at U{before['unit']}")
    db.session.commit()
    jobs.emit("rack.updated")
    return jsonify(_out(r))
