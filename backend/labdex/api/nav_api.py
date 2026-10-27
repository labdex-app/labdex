"""What the sidebar shows: the inventory tree with live status, and module entries."""
from __future__ import annotations

from flask import current_app, g, jsonify

from .. import naming, rbac, store
from ..models import Feature, Integration, Network, db
from . import bp


@bp.get("/nav")
def nav():
    p = g.principal
    reg = current_app.extensions["labdex"]["registry"]
    modules, provides = [], set()
    if p.can_any("integration", "view"):
        for i in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
            spec = reg.get(i.plugin_id)
            if spec is None or spec.error or not p.can("integration", "view", rbac.named_facts(i.name)):
                continue
            provides |= set(spec.provides)
            modules.append({"integration": i.name, "plugin_id": spec.id, "plugin_name": spec.name, "kind": spec.kind,
                            "pages": spec.pages, "provides": spec.provides})

    from .. import health
    live = {k: v["live"] for k, v in health.states().items()}
    nets = {n.name: n for n in db.session.scalars(db.select(Network))}
    zones: dict[str, dict] = {}
    for code in naming.all_zones():
        zones[code] = {"zone": code, "long": naming.zone_long(code), "colour": naming.zone_colour(code), "networks": {}, "hosts": []}
    for e in store.host_index():
        d = e["host"]
        if not p.can("host", "view", e["facts"]):
            continue
        item = {"name": d["name"], "fun": d["fun"], "status": d["status"], "live": live.get(d["name"], "unknown")}
        z = zones.setdefault(d["zone"], {"zone": d["zone"], "long": d["zone"], "colour": "white", "networks": {}, "hosts": []})
        net_name = e["net"]
        f = e["facts"]
        if f.seen_zones and not p.can("host", "view", rbac.Facts(**{**f.__dict__, "seen_zones": set(), "seen_areas": set()})):
            # visible only through an address in another zone: show it there
            net_name = next((i["network"] for i in d["interfaces"] if i.get("zone") and p.can(
                "network", "view", rbac.network_facts({"name": i["network"], "zone": i["zone"]}))), net_name)
            if net_name in nets:
                z = zones.setdefault(nets[net_name].zone, {"zone": nets[net_name].zone, "long": nets[net_name].zone, "colour": "white", "networks": {}, "hosts": []})
        if net_name is not None and net_name in nets:
            net = nets[net_name]
            node = z["networks"].setdefault(net.name, {"name": net.name, "vlan": net.vlan, "zone": net.zone, "hosts": []})
            node["hosts"].append(item)
        else:
            z["hosts"].append(item)
    for n in nets.values():  # empty networks still show, in their own zone
        if p.can("network", "view", rbac.network_facts(store.network_to_dict(n))) and n.zone in zones:
            zones[n.zone]["networks"].setdefault(n.name, {"name": n.name, "vlan": n.vlan, "zone": n.zone, "hosts": []})
    tree = []
    for z in zones.values():
        z["networks"] = sorted(z["networks"].values(), key=lambda x: (x["vlan"] is None, x["vlan"] or 0, x["name"]))
        count = sum(len(n["hosts"]) for n in z["networks"]) + len(z["hosts"])
        if count or z["networks"]:
            tree.append(z | {"count": count, "up": sum(1 for n in z["networks"] for x in n["hosts"] if x["live"] == "up")
                             + sum(1 for x in z["hosts"] if x["live"] == "up")})
    return jsonify({
        "instance_name": naming.get_setting("instance_name") or "labdex",
        "modules": modules,
        "show": {"discovery": "discovery" in provides and p.can_any("discovery", "view"),
                 "features": "features" in provides or bool(db.session.scalar(db.select(db.func.count()).select_from(Feature))),
                 "integrations": p.can_any("integration", "view"), "admin": p.is_admin},
        "tree": tree,
        "zones_defined": bool(naming.all_zones()),
    })
