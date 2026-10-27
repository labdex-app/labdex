"""Areas and the zones in them; the zone policy."""
from __future__ import annotations

from flask import g, jsonify

from .. import audit, naming, rbac, store, zones
from ..models import Area, Network, Zone, db
from ..plugins import jobs
from . import body, bp, err


@bp.get("/zones")
def get_zones():
    return jsonify(zones.overview())


def _finish(action, kind, name, before, after):
    audit.record(action, kind, name, before, after)
    db.session.commit()
    jobs.emit(f"{kind}.{action}d" if action != "delete" else f"{kind}.deleted")
    return jsonify(zones.overview())


@bp.post("/zones/areas")
@bp.put("/zones/areas/<code>")
def save_area(code=None):
    rbac.require_admin()
    existing = db.session.get(Area, code) if code else None
    if code and existing is None:
        return err(404, "no such area")
    before = zones.area_dict(existing) if existing else None
    a = zones.save_area(body(), existing)
    db.session.flush()
    return _finish("update" if code else "create", "area", a.code, before, zones.area_dict(a))


@bp.get("/zones/zones/<key>")
def get_zone(key):
    """One zone, by code or stable id: what's in it (networks, hosts at home there), and the policy
    cells from and to it. Everyone who can sign in sees the zones; networks and hosts by their rights."""
    from .. import policy
    z = zones.find_zone(key)
    if z is None:
        return err(404, f"no zone '{key}'")
    p = g.principal
    area = db.session.get(Area, z.area)
    nets = [store.network_to_dict(n) | {"uid": n.uid} for n in db.session.scalars(db.select(Network).where(Network.zone == z.code).order_by(Network.vlan, Network.name))]
    nets = [n for n in nets if p.can("network", "view", rbac.network_facts(n))]
    hosts = []
    for e in store.host_index():
        h = e["host"]
        if h["zone"] == z.code and p.can("host", "view", e["facts"]):
            hosts.append({"name": h["name"], "uid": h["uid"], "fun": h["fun"], "status": h["status"], "function": h["function"]})
    cells = [policy.cell_dict(c) for c in policy.cells() if z.code in (c.src, c.dst)]
    return jsonify(zones.zone_dict(z) | {"uid": z.uid, "long": naming.zone_long(z.code), "hex": naming.zone_hex(z.code),
                                         "area_name": area.name if area else "", "networks": nets, "hosts": hosts,
                                         "policy_out": [c for c in cells if c["src"] == z.code], "policy_in": [c for c in cells if c["dst"] == z.code],
                                         "can_edit": bool(p.is_admin)})


@bp.post("/zones/zones")
@bp.put("/zones/zones/<code>")
def save_zone(code=None):
    rbac.require_admin()
    existing = db.session.get(Zone, code) if code else None
    if code and existing is None:
        return err(404, "no such zone")
    before = zones.zone_dict(existing) if existing else None
    z = zones.save_zone(body(), existing)
    db.session.flush()
    return _finish("update" if code else "create", "zone", z.code, before, zones.zone_dict(z))


@bp.delete("/zones/<part>/<code>")
def delete_zone_part(part, code):
    rbac.require_admin()
    if part == "areas":
        zones.delete_area(code)
        kind = "area"
    elif part == "zones":
        zones.delete_zone(code)
        kind = "zone"
    else:
        return err(404, "areas or zones")
    return _finish("delete", kind, code, {"code": code}, None)


@bp.post("/zones/example")
def zones_example():
    rbac.require_admin()
    zones.load_example()
    db.session.flush()
    return _finish("create", "area", "example set", None, {"areas": [a for a, _ in zones.EXAMPLE_AREAS]})


# ------------------------------------------------------------------ policy
@bp.get("/zone-policy")
def get_zone_policy():
    from .. import policy
    return jsonify({"zones": [{"code": z, "long": naming.zone_long(z), "colour": naming.zone_colour(z), "hex": naming.zone_hex(z)}
                              for z in naming.all_zones()],
                    "cells": [policy.cell_dict(c) for c in policy.cells()], "port_aliases": policy.port_aliases(),
                    "can_edit": bool(g.principal.is_admin), "firewalls": _enforcers()})


def _enforcers() -> list[dict]:
    """The integrations that enforce the matrix: a plugin page with `place: policy` reports how one
    firewall matches it (shown on the Policy page), and its `action` brings it in line."""
    from flask import current_app
    from ..models import Integration
    reg = current_app.extensions["labdex"]["registry"]
    p = g.principal
    out = []
    for integ in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
        spec = reg.get(integ.plugin_id)
        if spec is None or spec.error or not p.can("integration", "view", rbac.named_facts(integ.name)):
            continue
        acts = {a["id"]: a for a in spec.manifest.get("actions") or []}
        for page in spec.pages:
            if page.get("place") != "policy":
                continue
            act = acts.get(page.get("action") or "")
            out.append({"integration": integ.name, "plugin": spec.id, "plugin_name": spec.name, "page": page["id"], "label": page.get("label") or page["id"],
                        "action": act, "can_apply": p.can("integration", "apply", rbac.named_facts(integ.name))})
    return out


@bp.put("/zone-policy")
def put_zone_policy():
    """One cell: {src, dst, ports, description}; empty ports removes it."""
    from .. import policy
    rbac.require_admin()
    d = body()
    src, dst, ports = str(d.get("src", "")).upper(), str(d.get("dst", "")).upper(), d.get("ports") or []
    errs = policy.validate(src, dst, ports)
    if errs:
        raise store.ValidationError(errs)
    before = next((policy.cell_dict(c) for c in policy.cells() if c.src == src and c.dst == dst), None)
    c = policy.put(src, dst, ports, str(d.get("description") or ""))
    db.session.flush()
    after = policy.cell_dict(c) if c else None
    audit.record("update" if before and after else "create" if after else "delete", "zone_policy", f"{src}→{dst}", before, after,
                 summary=f"{src} → {dst}: " + (", ".join(after["ports"]) if after else "no access"))
    db.session.commit()
    jobs.emit("policy.updated")
    return jsonify(after or {"src": src, "dst": dst, "ports": [], "description": ""})
