"""The lab as a graph: hosts, cables, and hypervisors as boxes with their guests inside.

A host that other hosts run on becomes a box. Inside it: its guests, and its own interfaces
(bridges, bonds, ports) as small port nodes joined the way they're stacked (vmbr0 on bond0
on eno1). Cables at the hypervisor attach to its port nodes; an internal cable, or a guest
interface that sits on the hypervisor's bridge (parent "host:vmbr0"), joins the guest to
that bridge. Other hosts are plain nodes; their cables carry the port names."""
from __future__ import annotations

from flask import g, jsonify, request

from .. import audit, cabling, rbac, store
from ..models import Host, Layout, Link, MapView, Network, db, utcnow
from ..plugins import jobs
from . import body, bp, err

VIEWS = {"physical": "topology", "l3": "topology-l3"}  # each view keeps its own arrangement
MAX_NODES = 5000


def _view() -> str:
    v = request.args.get("view", "physical")
    return v if v in VIEWS else "physical"


def _visible_hosts() -> dict[str, dict]:
    p = g.principal
    return {e["host"]["name"]: e["host"] for e in store.host_index() if p.can("host", "view", e["facts"])}


def _host_node(h: dict, parent: str | None = None) -> dict:
    return {"id": f"h:{h['name']}", "type": "host", "name": h["name"], "uid": h.get("uid", ""), "label": h.get("fun") or h["name"],
            "fun": h.get("fun") or "", "colour": h.get("colour") or "", "zone": h["zone"],
            "status": h["status"], "kind": h.get("kind") or "device", "icon": h.get("icon") or "",
            "function": h.get("function") or "", "parent": parent, "runs_on": h.get("runs_on") or "",
            "address": next((i.get("ip4") for i in h["interfaces"] if i.get("primary") and i.get("ip4")),
                            next((i.get("ip4") for i in h["interfaces"] if i.get("ip4")), ""))}


def _answer(view: str, nodes: dict, edges: list):
    lay = db.session.get(Layout, VIEWS[view])
    return jsonify({"view": view, "nodes": list(nodes.values()), "edges": edges,
                    "layout": {k: v for k, v in (lay.positions if lay else {}).items() if (k[2:] if k.startswith("t:") else k) in nodes},
                    "layout_by": lay.updated_by if lay else "", "can_arrange": _can_arrange()})


def _l3(hosts: dict[str, dict], zone: str):
    """Layer 3: networks as nodes, every host joined to the networks it has an address in."""
    p = g.principal
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for n in db.session.scalars(db.select(Network).order_by(Network.name)):
        if not p.can("network", "view", rbac.network_facts({"name": n.name, "zone": n.zone})):
            continue
        nodes[f"n:{n.name}"] = {"id": f"n:{n.name}", "type": "network", "name": n.name, "uid": n.uid, "label": n.name, "zone": n.zone,
                                "vlan": n.vlan, "prefix": n.prefix4 or n.prefix6 or "", "gateway": n.gateway4 or ""}
    for name, h in hosts.items():
        nets = [i for i in h["interfaces"] if i.get("network") and f"n:{i['network']}" in nodes]
        if not nets:
            continue
        nodes[f"h:{name}"] = _host_node(h)
        for k, i in enumerate(nets):
            edges.append({"id": f"a:{name}:{k}", "source": f"h:{name}", "target": f"n:{i['network']}", "kind": "address",
                          "label": ", ".join(a for a in [i.get("ip4") or i.get("ip6"), *(i.get("extra_ips") or [])] if a) or "dhcp", "a_port": i.get("label") or "", "a_host": name})
    for name, h in hosts.items():  # a VIP and the hosts that hold it
        if h.get("kind") == "vip" and f"h:{name}" in nodes:
            for m in h.get("vip_members") or []:
                if f"h:{m}" in nodes:
                    edges.append({"id": f"v:{name}:{m}", "source": f"h:{m}", "target": f"h:{name}", "kind": "vip", "label": h.get("vip_protocol") or "",
                                  "a_host": m, "b_host": name})
    per_host: dict[str, set] = {}
    for ed in edges:
        if ed["kind"] != "address":
            continue
        per_host.setdefault(ed["source"], set()).add(ed["target"])
    for nid, nets in per_host.items():
        nodes[nid]["router"] = len(nets) > 1  # in two networks or more: it joins them
    if zone:
        keep = {nid for nid, n in nodes.items() if n.get("zone") == zone}
        keep |= {ed["target"] for ed in edges if ed["source"] in keep} | {ed["source"] for ed in edges if ed["target"] in keep}
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edges = [ed for ed in edges if ed["source"] in nodes and ed["target"] in nodes]
    return _answer("l3", nodes, edges)


@bp.get("/topology")
def topology():
    zone = request.args.get("zone", "").upper()
    hosts = _visible_hosts()
    if _view() == "l3":
        return _l3(hosts, zone)
    hosts = {k: h for k, h in hosts.items() if h.get("kind") != "vip"}  # an address, not a thing with cables
    hypervisors = {h["runs_on"] for h in hosts.values() if h.get("runs_on") in hosts}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def port_node(hyp: str, port: str) -> str:
        nid = f"p:{hyp}:{port}"
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": "port", "parent": f"box:{hyp}", "label": port, "host": hyp, "kind": ""}
        return nid

    def end(host: str, port: str) -> str:
        return port_node(host, port) if host in hypervisors else f"h:{host}"

    for name in sorted(hypervisors):
        h = hosts[name]
        nodes[f"box:{name}"] = {"id": f"box:{name}", "type": "box", "host": name, "label": h.get("fun") or name, "name": name, "uid": h.get("uid", ""),
                                "fun": h.get("fun") or "", "colour": h.get("colour") or "",
                                "zone": h["zone"], "status": h["status"], "icon": h.get("icon") or "", "function": h.get("function") or ""}
        for i in h["interfaces"]:
            if i.get("label"):
                nid = port_node(name, i["label"])
                nodes[nid]["kind"] = i.get("kind") or ""
                nodes[nid]["address"] = i.get("ip4") or i.get("ip6") or ""
                nodes[nid]["network"] = i.get("network") or ""
                nodes[nid]["speed"] = i.get("speed") or ""
        for i in h["interfaces"]:  # the stack inside the hypervisor: vmbr0 → bond0 → eno1
            par = i.get("parent") or ""
            if i.get("label") and par and ":" not in par:
                edges.append({"id": f"s:{name}:{i['label']}", "source": port_node(name, i["label"]), "target": port_node(name, par),
                              "kind": "stack", "label": ""})
    for name, h in hosts.items():
        if name in hypervisors:
            continue
        on = h.get("runs_on") if h.get("runs_on") in hypervisors else ""
        nodes[f"h:{name}"] = _host_node(h, f"box:{on}" if on else None)
        for i in h["interfaces"]:  # a guest interface on the hypervisor's bridge: an internal link
            par = i.get("parent") or ""
            if ":" in par:
                ph, pport = par.split(":", 1)
                if ph in hosts:
                    edges.append({"id": f"i:{name}:{i.get('label') or i.get('network')}", "source": f"h:{name}", "target": end(ph, pport),
                                  "kind": "internal", "label": i.get("label") or "", "a_port": i.get("label") or "", "b_port": pport,
                                  "a_host": name, "b_host": ph})
            elif not par and on and i.get("network"):
                # running on a hypervisor and in a network: guess the bridge it sits on (a bridge of the
                # hypervisor in the same network, else any interface of it there); drawn as a guess
                hyp = hosts[on]["interfaces"]
                br = next((x for x in hyp if x.get("label") and x.get("network") == i["network"] and x.get("kind") == "bridge"), None) \
                    or next((x for x in hyp if x.get("label") and x.get("network") == i["network"]), None)
                if br is not None:
                    edges.append({"id": f"i:{name}:{i.get('label') or i.get('network')}", "source": f"h:{name}", "target": end(on, br["label"]),
                                  "kind": "internal", "auto": True, "label": i.get("label") or "", "a_port": i.get("label") or "",
                                  "b_port": br["label"], "a_host": name, "b_host": on})

    seen_links = set()
    ids = {h.id: h.name for h in db.session.scalars(db.select(Host).where(Host.name.in_(list(hosts))))}
    for lk in db.session.scalars(db.select(Link)):
        a, b = ids.get(lk.a_host_id), ids.get(lk.b_host_id)
        if a is None or b is None or lk.id in seen_links:
            continue
        seen_links.add(lk.id)
        internal = (lk.medium or "") == "virtual"
        a_port, b_port = lk.a_port, lk.b_port
        if internal and a in hypervisors and b not in hypervisors:  # always guest → the hypervisor's bridge
            a, b, a_port, b_port = b, a, b_port, a_port
        src, dst = end(a, a_port), end(b, b_port)
        edges.append({"id": f"c:{lk.id}", "source": src, "target": dst, "kind": "internal" if internal else "cable", "link": lk.id,
                      "a_port": a_port, "b_port": b_port, "a_host": a, "b_host": b, "medium": lk.medium or "",
                      "speed": lk.speed or "", "notes": lk.notes or "", "cable_label": lk.label or "", "length": lk.length or "",
                      "cable_colour": lk.colour or "", "label": f"{a_port} – {b_port}"})
    # the same internal link may be both a guest's interface parent and an internal cable: keep one
    out_edges, keys = [], set()
    for ed in sorted(edges, key=lambda x: bool(x.get("auto"))):  # a drawn link wins over a guessed one
        k = (ed["kind"] == "stack", frozenset((ed["source"], ed["target"])))
        if k in keys:
            continue
        keys.add(k)
        out_edges.append(ed)
    linked = {ed["source"] for ed in out_edges} | {ed["target"] for ed in out_edges}
    for n in nodes.values():
        n["lonely"] = n["type"] == "host" and not n.get("parent") and n["id"] not in linked
    if zone:  # a zone: its hosts, plus whatever they're cabled to
        keep = {nid for nid, n in nodes.items() if n.get("zone") == zone}
        keep |= {nid for nid, n in nodes.items() if n["type"] == "port" and f"box:{n['host']}" in keep}
        keep |= {nid for nid, n in nodes.items() if n.get("parent") in keep}
        near = {ed["target"] for ed in out_edges if ed["source"] in keep} | {ed["source"] for ed in out_edges if ed["target"] in keep}
        keep |= near
        keep |= {nodes[nid]["parent"] for nid in near if nodes.get(nid, {}).get("parent")}
        nodes = {k: v for k, v in nodes.items() if k in keep}
        out_edges = [ed for ed in out_edges if ed["source"] in nodes and ed["target"] in nodes]
    return _answer("physical", nodes, out_edges)


def _can_arrange() -> bool:
    """The shared arrangement is moved by people who edit hosts."""
    return g.principal.can_any("host", "edit")


@bp.put("/topology/layout")
def save_layout():
    """{"positions": {node id: {x, y}}}: merged into the shared layout (only the nodes sent move);
    {"replace": true} starts over with exactly these."""
    if not _can_arrange():
        return err(403, "only people who edit hosts arrange the shared topology")
    data = body()
    raw = data.get("positions") or {}
    if not isinstance(raw, dict) or len(raw) > MAX_NODES:
        return err(422, "validation failed", [{"field": "positions", "message": f"{{node: {{x, y}}}}, at most {MAX_NODES}"}])
    clean = {}
    for k, v in raw.items():
        try:
            clean[str(k)[:200]] = {"x": round(float(v["x"]), 1), "y": round(float(v["y"]), 1)}
        except (KeyError, TypeError, ValueError):
            return err(422, "validation failed", [{"field": f"positions.{k}", "message": "x and y as numbers"}])
    lay = db.session.get(Layout, VIEWS[_view()])
    if lay is None:
        lay = Layout(name=VIEWS[_view()], positions={})
        db.session.add(lay)
    lay.positions = clean if data.get("replace") else {**(lay.positions or {}), **clean}
    lay.updated_by, lay.updated_at = g.principal.label, utcnow()
    db.session.commit()
    return jsonify({"saved": len(clean), "nodes": len(lay.positions)})


@bp.delete("/topology/layout")
def reset_layout():
    if not _can_arrange():
        return err(403, "only people who edit hosts arrange the shared topology")
    lay = db.session.get(Layout, VIEWS[_view()])
    if lay is not None:
        db.session.delete(lay)
        db.session.commit()
    return "", 204


# ------------------------------------------------------------------- cables from the map
def _port(raw) -> str:
    return str(raw or "").strip()[:32]


@bp.post("/topology/cables")
def add_cable():
    """Draw a cable on the map: {"a": {"host", "port", "iface"?}, "b": {…}, "medium", "speed", "notes"}.
    A port a host doesn't have yet becomes an interface of it (label only), or names the unnamed
    interface at position "iface". Goes through the normal
    host validation, so the same rules hold as on the host's Network tab; one history entry per host."""
    data = body()
    a, b = data.get("a") or {}, data.get("b") or {}
    ha, hb = store.find_host(str(a.get("host", ""))), store.find_host(str(b.get("host", "")))
    errors = [{"field": f"{k}.host", "message": "unknown host"} for k, h in (("a", ha), ("b", hb)) if h is None]
    pa, pb = _port(a.get("port")), _port(b.get("port"))
    errors += [{"field": f"{k}.port", "message": "the port, e.g. eth0"} for k, pt in (("a", pa), ("b", pb)) if not pt]
    if ha is not None and ha is hb and pa == pb:
        errors.append({"field": "b.port", "message": "a cable inside one host joins two different ports"})
    if errors:
        return err(422, "validation failed", errors)
    try:
        summary = cabling.add(ha, pa, hb, pb, data, a, b)
    except store.ValidationError as exc:
        db.session.rollback()
        return err(422, "validation failed", exc.errors)
    db.session.commit()
    jobs.emit("host.updated")
    lk = db.session.scalar(db.select(Link).where(Link.a_host_id == ha.id, db.func.lower(Link.a_port) == pa.lower(), Link.b_host_id == hb.id,
                                              db.func.lower(Link.b_port) == pb.lower()))
    return jsonify({"id": lk.id if lk else None, "summary": summary}), 201


@bp.put("/topology/cables/<int:link_id>")
def update_cable(link_id):
    """{"medium", "speed", "notes"}: the kind of cable and what's written on it. Saved through the
    host at the cable's first end, so the host rules hold (an internal link joins a guest and its host)."""
    lk = db.session.get(Link, link_id)
    if lk is None:
        return err(404, "no such cable")
    data = body()
    ha, hb = db.session.get(Host, lk.a_host_id), db.session.get(Host, lk.b_host_id)
    befores = {h.name: store.host_to_dict(h) for h in dict.fromkeys((ha, hb))}
    for b in befores.values():
        rbac.require("host", "edit", rbac.host_facts(b))
    d = befores[ha.name]
    cables = []
    for c in d["cables"]:
        if (c["port"], c["peer"], c["peer_port"]) == (lk.a_port, hb.name, lk.b_port):
            c = {**c, **{k: str(data[k]).strip() for k in ("medium", "speed", "notes", "label", "length", "colour") if k in data}}
        cables.append(c)
    try:
        store.save_host({**d, "cables": cables}, existing=ha)
        db.session.flush()
    except store.ValidationError as exc:
        db.session.rollback()
        return err(422, "validation failed", exc.errors)
    summary = f"cable {ha.name} {lk.a_port} – {hb.name} {lk.b_port} changed"
    for h in dict.fromkeys((ha, hb)):
        audit.record("update", "host", h.name, befores[h.name], store.host_to_dict(h), summary=summary)
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify({"cables": store.host_links(ha)})


@bp.get("/cables")
def list_cables():
    """Every cable whose both ends the caller may see, for the cable list and label sheets."""
    hosts = _visible_hosts()
    out = []
    for lk in db.session.scalars(db.select(Link).order_by(Link.label, Link.id)):
        a, b = lk.a_host, lk.b_host
        if a.name not in hosts or b.name not in hosts:
            continue
        out.append({"id": lk.id, "label": lk.label or "", "a_host": a.name, "a_uid": a.uid, "a_port": lk.a_port, "b_host": b.name, "b_uid": b.uid,
                    "b_port": lk.b_port, "medium": lk.medium or "", "speed": lk.speed or "", "length": lk.length or "",
                    "colour": lk.colour or "", "notes": lk.notes or "", "a_zone": a.zone, "b_zone": b.zone})
    return jsonify({"cables": out, "next_label": store.next_cable_label(), "can_edit": _can_arrange()})


@bp.delete("/topology/cables/<int:link_id>")
def remove_cable(link_id):
    lk = db.session.get(Link, link_id)
    if lk is None:
        return err(404, "no such cable")
    hosts = [db.session.get(Host, lk.a_host_id), db.session.get(Host, lk.b_host_id)]
    befores = {h.name: store.host_to_dict(h) for h in hosts}
    hosts = list(dict.fromkeys(hosts))
    for b in befores.values():
        rbac.require("host", "edit", rbac.host_facts(b))
    summary = f"cable {hosts[0].name} {lk.a_port} – {hosts[1].name} {lk.b_port} removed"
    db.session.delete(lk)
    db.session.flush()
    for h in hosts:
        db.session.expire(h)
        audit.record("update", "host", h.name, befores[h.name], store.host_to_dict(h), summary=summary)
    db.session.commit()
    jobs.emit("host.updated")
    return "", 204


# ------------------------------------------------------------------- saved views
def _view_config(raw) -> dict:
    """Only what a view is made of: picture, zone, hidden networks, options and the camera."""
    raw = raw if isinstance(raw, dict) else {}
    cfg = {"view": raw.get("view") if raw.get("view") in VIEWS else "physical",
           "zone": str(raw.get("zone") or "").upper()[:32],
           "hidden_nets": [str(x)[:64] for x in (raw.get("hidden_nets") or [])][:200],
           "lonely": bool(raw.get("lonely")), "grid": bool(raw.get("grid"))}
    cam = raw.get("camera")
    try:
        cfg["camera"] = {"zoom": round(float(cam["zoom"]), 3), "x": round(float(cam["x"]), 1), "y": round(float(cam["y"]), 1)}
    except (TypeError, KeyError, ValueError):
        pass
    return cfg


def _view_dict(v: MapView) -> dict:
    return {"uid": v.uid, "name": v.name, "config": v.config or {}, "created_by": v.created_by or "",
            "updated_at": v.updated_at.isoformat() + "Z" if v.updated_at else None}


@bp.get("/topology/views")
def list_views():
    views = db.session.scalars(db.select(MapView).order_by(MapView.name))
    return jsonify({"views": [_view_dict(v) for v in views], "can_edit": _can_arrange()})


@bp.post("/topology/views")
@bp.put("/topology/views/<uid>")
def save_view(uid=None):
    if not _can_arrange():
        return err(403, "only people who edit hosts save views of the shared topology")
    data = body()
    v = None
    if uid:
        v = db.session.scalar(db.select(MapView).where(MapView.uid == uid))
        if v is None:
            return err(404, "no such view")
    name = str(data.get("name", v.name if v else "")).strip()[:64]
    if not name:
        return err(422, "validation failed", [{"field": "name", "message": "a name, like Rack room or Kube networks"}])
    other = db.session.scalar(db.select(MapView).where(MapView.name == name))
    if other is not None and other is not v:
        return err(422, "validation failed", [{"field": "name", "message": "there's a view with that name"}])
    before = _view_dict(v) if v else None
    if v is None:
        v = MapView(name=name, created_by=g.principal.label)
        db.session.add(v)
    v.name = name
    if "config" in data or before is None:
        v.config = _view_config(data.get("config"))
    v.updated_at = utcnow()
    db.session.flush()
    audit.record("update" if before else "create", "setting", f"map view {v.name}", before, _view_dict(v))
    db.session.commit()
    return jsonify(_view_dict(v)), 200 if before else 201


@bp.delete("/topology/views/<uid>")
def delete_view(uid):
    if not _can_arrange():
        return err(403, "only people who edit hosts change the saved views")
    v = db.session.scalar(db.select(MapView).where(MapView.uid == uid))
    if v is None:
        return err(404, "no such view")
    audit.record("delete", "setting", f"map view {v.name}", _view_dict(v), None)
    db.session.delete(v)
    db.session.commit()
    return "", 204


# ------------------------------------------------------------- trace
@bp.get("/cables/trace")
def cable_trace():
    """?host=&port=: the whole path through patch panels, end to end. Hosts you can't see show
    as "a host you can't see"."""
    from flask import request
    h = store.find_host(str(request.args.get("host", "")))
    port = str(request.args.get("port", "")).strip()
    visible = set(_visible_hosts())
    if h is None or h.name not in visible:
        return err(404, "no such host")
    if not port:
        return err(422, "validation failed", [{"field": "port", "message": "which port"}])
    t = cabling.trace(h, port)
    for p in t["points"]:
        if p["host"] not in visible:
            p.update({"host": "", "uid": "", "hidden": True})
    return jsonify(t)


# ------------------------------------------------------------- what switches see
@bp.get("/cables/suggestions")
def cable_suggestions():
    """Cables LLDP/CDP or UniFi saw that the inventory doesn't have (or has differently)."""
    out = cabling.suggestions(set(_visible_hosts()))
    return jsonify(out | {"can_edit": _can_arrange()})


@bp.post("/cables/suggestions/accept")
def accept_suggestions():
    """{"keys": [...], "replace": false}: make these cables. With replace, a cable already on one
    of the ports goes (that's what a conflict asks for)."""
    data = body()
    keys, replace = [str(k) for k in data.get("keys") or []], bool(data.get("replace"))
    found = cabling.suggestions(set(_visible_hosts()))
    by_key = {x["key"]: x for x in found["suggested"] + found["conflicts"]}
    made, errors = [], []
    for k in keys:
        x = by_key.get(k)
        if x is None:
            errors.append({"field": k, "message": "not a current suggestion"})
            continue
        ha, hb = store.find_host(x["a_host"]), store.find_host(x["b_host"])
        b_port, b_side = x["b_port"], {}
        if not b_port:  # a client: name its interface with that MAC
            labels = {(i.label or "").lower() for i in hb.interfaces}
            b_port = next(f"eth{n}" for n in range(100) if f"eth{n}" not in labels)
            b_side = {"iface": next((n for n, i in enumerate(hb.interfaces) if (i.mac or "").lower() == x["b_mac"] and not i.label), None)}
        try:
            with db.session.begin_nested():
                made.append(cabling.add(ha, x["a_port"], hb, b_port, {"medium": "copper", "notes": f"seen by {x['source']} ({x['protocol']})"[:200]},
                                        b_side=b_side, replace=replace))
        except store.ValidationError as exc:
            errors += [{"field": k, "message": e["message"]} for e in exc.errors]
    db.session.commit()
    if made:
        jobs.emit("host.updated")
    return jsonify({"made": made, "errors": errors}), 200 if made or not errors else 422


@bp.post("/cables/suggestions/ignore")
def ignore_suggestions():
    if not _can_arrange():
        return err(403, "only people who edit hosts decide about cables")
    n = cabling.ignore([str(k) for k in body().get("keys") or []], g.principal.label)
    db.session.commit()
    return jsonify({"ignored": n})
