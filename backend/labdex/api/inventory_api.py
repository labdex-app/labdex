"""Hosts, networks, aliases, lint and services: every read filtered and every
write checked against the caller's permissions, recorded in the history and
announced to plugins."""
from __future__ import annotations

from flask import current_app, g, jsonify, request

from .. import audit, inventory, ipam, lint, naming, rbac, store
from ..generators import aliases as galiases
from ..models import AliasGroup, Host, Network, db
from ..plugins import jobs
from . import body, bp, err


def _ip_key(ip):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
        return (a.version, int(a))
    except ValueError:
        return (9, 0)


# ----------------------------------------------------------------------- meta
@bp.get("/meta")
def meta():
    p = g.principal
    m = store.meta()
    hosts = [e for e in store.host_index() if p.can("host", "view", e["facts"])]
    nets = [n for n in db.session.scalars(db.select(Network)) if p.can("network", "view", rbac.network_facts(store.network_to_dict(n)))]
    m["counts"] = {"hosts": len(hosts), "networks": len(nets)}
    m["version"] = current_app.config["LABDEX_VERSION"]
    from .about_api import project_url
    m["project_url"] = project_url()  # the top bar's link to the project, and Help
    m["permissions"] = p.summary()
    m["is_admin"] = p.is_admin
    from .. import demo
    m["demo"] = demo.info()
    return jsonify(m)


# ------------------------------------------------------------------- networks
def _net(name) -> Network | None:
    return store.find_network(name)


@bp.get("/networks")
def list_networks():
    p = g.principal
    nets = db.session.scalars(db.select(Network).order_by(Network.vlan.is_(None), Network.vlan, Network.name))
    out = []
    for n in nets:
        d = store.network_to_dict(n, with_stats=True)
        if p.can("network", "view", rbac.network_facts(d)):
            d["can_edit"] = p.can("network", "edit", rbac.network_facts(d))
            out.append(d)
    return jsonify(out)


@bp.post("/networks")
def create_network():
    data = body()
    rbac.require("network", "edit", rbac.network_facts({"name": str(data.get("name", "")).lower(), "zone": str(data.get("zone", "")).upper()}))
    n = store.save_network(data)
    db.session.flush()
    audit.record("create", "network", n.name, None, store.network_to_dict(n))
    db.session.commit()
    jobs.emit("network.created")
    return jsonify(store.network_to_dict(n, with_stats=True)), 201


@bp.get("/networks/<name>")
def get_network(name):
    n = _net(name)
    if n is None or not g.principal.can("network", "view", rbac.network_facts(store.network_to_dict(n))):
        return err(404, f"no network '{name}'")
    p = g.principal
    d = store.network_to_dict(n, with_stats=True)
    used = []
    for ip, info in sorted(ipam.used(n).items(), key=lambda kv: _ip_key(kv[0])):
        host = db.session.scalar(db.select(Host).where(Host.name == info["host"])) if info["host"] else None
        if host is not None and not p.can("host", "view", rbac.host_facts(store.host_to_dict(host))):
            info = {"host": None, "fun": None, "label": "in use"}
        used.append({"ip": ip, **info})
    d["used"] = used
    d["free"] = ipam.free(n, limit=min(int(request.args.get("free", 10)), 200))
    d["usage"] = ipam.prefix_usage(n, free_limit=min(int(request.args.get("free", 10)), 200))
    fw = galiases.firewall_ids()
    d["aliases"] = [a.as_dict() | {"fw_id": fw.get(a.name, a.name)} for a in galiases.network_aliases(n)]
    d["can_edit"] = p.can("network", "edit", rbac.network_facts(d))
    members = {}
    for iface in n.interfaces:
        h = iface.host
        hd = store.host_to_dict(h)
        if not p.can("host", "view", rbac.host_facts(hd)):
            continue
        m = members.setdefault(h.name, {"name": h.name, "fun": h.fun or "", "kind": h.kind or "device", "status": h.status,
                                        "zone": h.zone, "function": h.function, "addresses": []})
        m["addresses"].append({"label": iface.label, "ip4": iface.ip4, "ip6": " ".join(iface.ip6s), "mac": iface.mac, "primary": iface.primary})
    d["members"] = sorted(members.values(), key=lambda m: m["name"])
    return jsonify(d)


@bp.put("/networks/<name>")
def update_network(name):
    n = _net(name)
    if n is None:
        return err(404, f"no network '{name}'")
    before = store.network_to_dict(n)
    data = body()
    rbac.require("network", "edit", rbac.network_facts(before))
    rbac.require("network", "edit", rbac.network_facts({"name": str(data.get("name", "")).lower(), "zone": str(data.get("zone", "")).upper()}))
    hosts_before = {h.id: store.host_to_dict(h) for h in db.session.scalars(db.select(Host).where(Host.zone_pinned.is_(False)))}
    store.save_network(data, existing=n)
    db.session.flush()
    audit.record("update", "network", n.name, before, store.network_to_dict(n))
    for h in store.follow_network_zone(n):  # hosts that follow their network's zone move along
        db.session.flush()
        audit.record("update", "host", h.name, hosts_before.get(h.id), store.host_to_dict(h),
                     summary=f"zone {hosts_before.get(h.id, {}).get('zone')} → {h.zone} with network {n.name}")
    db.session.commit()
    jobs.emit("network.updated")
    return jsonify(store.network_to_dict(n, with_stats=True))


@bp.delete("/networks/<name>")
def delete_network(name):
    n = _net(name)
    if n is None:
        return err(404, f"no network '{name}'")
    before = store.network_to_dict(n)
    rbac.require("network", "edit", rbac.network_facts(before))
    if n.interfaces and request.args.get("force") != "1":
        hosts = sorted({i.host.name for i in n.interfaces})
        return err(409, f"{len(hosts)} host(s) still use {name}: {', '.join(hosts[:10])}")
    db.session.delete(n)
    audit.record("delete", "network", name, before, None)
    db.session.commit()
    jobs.emit("network.deleted")
    return "", 204


@bp.get("/networks/<name>/map")
def network_map(name):
    """Every address of one /24 block (or the whole prefix if smaller) of an IPv4 prefix, with what
    it is: network, broadcast, gateway, host (static or inside the DHCP pool), dhcp (pool, free),
    seen (Discovery saw it, no host has it), conflict (two hosts, or Discovery saw another MAC),
    free. ?prefix=10.10.100.0/24 (default: the first IPv4 prefix), ?block=0,1,… for /23 and up."""
    import ipaddress as ipa

    from ..models import DiscoveredHost
    n = _net(name)
    if n is None or not g.principal.can("network", "view", rbac.network_facts(store.network_to_dict(n))):
        return err(404, f"no network '{name}'")
    v4 = [pp for pp in n.prefixes if pp.family == 4 and pp.prefix]
    want = request.args.get("prefix") or (v4[0].prefix if v4 else "")
    pfx = next((pp for pp in v4 if pp.prefix == want), None)
    if pfx is None:
        return jsonify({"prefixes": [pp.prefix for pp in v4], "prefix": "", "blocks": [], "cells": []})
    net = ipa.ip_network(pfx.prefix, strict=False)
    size = min(net.num_addresses, 256)
    blocks = max(net.num_addresses // 256, 1)
    if blocks > 256:  # a /15 or bigger: 256 blocks are plenty to page through
        blocks = 256
    try:
        block = max(0, min(int(request.args.get("block", 0)), blocks - 1))
    except ValueError:
        block = 0
    first = int(net.network_address) + block * 256
    lo, hi = ipa.ip_address(first), ipa.ip_address(first + size - 1)
    gw = pfx.gateway or n.gateway4 or ""
    ds, de = ipam._addr(pfx.dhcp_start or n.dhcp_start), ipam._addr(pfx.dhcp_end or n.dhcp_end)
    p = g.principal
    owners: dict[str, list] = {}
    from ..models import Interface
    for iface in db.session.scalars(db.select(Interface).where(Interface.ip4 != "")):
        addrs = [a for a in iface.all_addresses if a and ":" not in a]
        for a in addrs:
            ad = ipam._addr(a)
            if ad is None or not lo <= ad <= hi:
                continue
            h = iface.host
            visible = p.can("host", "view", rbac.host_facts(store.host_to_dict(h)))
            owners.setdefault(a, []).append({"host": h.name if visible else None, "fun": (h.fun or "") if visible else "",
                                             "label": iface.label or "", "mac": (iface.mac or "").lower() if visible else "",
                                             "status": h.status if visible else ""})
    seen = {}
    for dh in db.session.scalars(db.select(DiscoveredHost)):
        ad = ipam._addr(dh.ip)
        if ad is not None and ad.version == 4 and lo <= ad <= hi:
            seen[str(ad)] = {"hostname": dh.hostname or "", "mac": (dh.mac or "").lower(), "vendor": dh.vendor or "", "up": bool(dh.up),
                             "last_seen": dh.last_seen.isoformat() + "Z" if dh.last_seen else None}
    cells, counts = [], {}
    for k in range(size):
        a = ipa.ip_address(first + k)
        ip = str(a)
        own, sn = owners.get(ip, []), seen.get(ip)
        in_pool = bool(ds and de and ds <= a <= de)
        if net.prefixlen < 31 and a == net.network_address:
            st = "network"
        elif net.prefixlen < 31 and a == net.broadcast_address:
            st = "broadcast"
        elif len(own) > 1 or (own and sn and sn["mac"] and own[0]["mac"] and sn["mac"] != own[0]["mac"]):
            st = "conflict"
        elif own:
            st = "host"
        elif ip == gw:
            st = "gateway"
        elif sn:
            st = "seen"
        elif in_pool:
            st = "dhcp"
        else:
            st = "free"
        counts[st] = counts.get(st, 0) + 1
        cells.append({"ip": ip, "state": st, "pool": in_pool, "gateway": ip == gw, "owners": own, "seen": sn})
    return jsonify({"prefixes": [pp.prefix for pp in v4], "prefix": pfx.prefix, "block": block, "blocks": blocks,
                    "range": [str(lo), str(hi)], "gateway": gw, "dhcp": [str(ds), str(de)] if ds and de else [],
                    "counts": counts, "cells": cells})


@bp.get("/networks/<name>/next")
def next_free(name):
    n = _net(name)
    if n is None or not g.principal.can("network", "view", rbac.network_facts(store.network_to_dict(n))):
        return err(404, f"no network '{name}'")
    free = ipam.free(n, limit=1)
    v6 = ipam.known_prefixes(n, 6)
    token = ipam.free_tokens(n, v6[0], limit=1)[0] if v6 else None
    return jsonify({"ip": free[0] if free else None, "ip6_token": token})


# ---------------------------------------------------------------------- hosts
def _host(name) -> Host | None:
    return store.find_host(name)


def _payload_facts(data: dict) -> rbac.Facts:
    return rbac.host_facts({"name": str(data.get("name", "")).lower(), "zone": str(data.get("zone", "")).upper(),
                            "fun": str(data.get("fun") or "").lower(), "tags": data.get("tags") or [],
                            "interfaces": data.get("interfaces") or []})


HOST_SORTS = {"name", "zone", "status", "function", "fun", "ip"}


@bp.get("/hosts")
def list_hosts():
    """All visible hosts. Optional, all combinable:
    fields=name,fun,zone  only these keys (skips live status and links unless asked for)
    q=text                 name, fun names, tags, addresses or description contain the text
    zone=DC_TC             exact zone; status=active
    sort=name|-zone|ip     sort key, '-' for descending
    limit=, offset=        a page; the total before paging is in X-Total-Count
    """
    p = g.principal
    args = request.args
    fields = {f.strip() for f in args.get("fields", "").split(",") if f.strip()}
    want = (lambda k: not fields or k in fields)
    needle = args.get("q", "").strip().lower()
    out = []
    from .. import health
    live = health.states() if want("live") else {}
    zone, status = args.get("zone", "").upper(), args.get("status", "")
    for e in store.host_index():
        src = e["host"]
        if (zone and src["zone"] != zone) or (status and src["status"] != status):
            continue
        f = e["facts"]
        if not p.can("host", "view", f):
            continue
        if needle:
            hay = " ".join([src["name"], *src.get("fun_names", []), *(src.get("tags") or []), src.get("description") or "", src.get("function") or "",
                            *[i.get(k) or "" for i in src["interfaces"] for k in ("ip4", "ip6", "mac")], *[a for i in src["interfaces"] for a in i.get("ip6_all") or []]]).lower()
            if needle not in hay:
                continue
        d = dict(src)
        if want("live"):
            st = live.get(src["name"]) or {}
            d["live"] = st.get("live", "unknown")
            d["live_sources"] = st.get("sources", {})
        if want("zone_long"):
            d["zone_long"] = e["zone_long"]
        if want("name_ok"):
            d["name_ok"] = e["name_ok"]
        if want("links"):
            d["links"] = e["links"]
        if want("can_edit"):
            d["can_edit"] = p.can("host", "edit", f)
        out.append(d)
    sort = args.get("sort", "name")
    key, rev = sort.lstrip("-"), sort.startswith("-")
    if key in HOST_SORTS and (key != "name" or rev):
        if key == "ip":
            def k(d):
                ip = next((i.get("ip4") for i in d["interfaces"] if i.get("ip4")), None)
                return _ip_key(ip) if ip else (9,)
        else:
            def k(d):
                return (str(d.get(key) or "").lower(), d["name"])
        out.sort(key=k, reverse=rev)
    total = len(out)
    offset = max(int(args.get("offset", 0) or 0), 0)
    if args.get("limit"):
        out = out[offset:offset + max(min(int(args["limit"]), 1000), 1)]
    elif offset:
        out = out[offset:]
    if fields:
        out = [{k: v for k, v in d.items() if k in fields} for d in out]
    resp = jsonify(out)
    resp.headers["X-Total-Count"] = str(total)
    return resp


@bp.post("/hosts")
def create_host():
    data = body()
    rbac.require("host", "edit", _payload_facts(data))
    h = store.save_host(data)
    db.session.flush()
    audit.record("create", "host", h.name, None, store.host_to_dict(h))
    db.session.commit()
    jobs.emit("host.created")
    return jsonify(_host_detail(h)), 201


def _host_detail(h: Host) -> dict:
    d = store.host_to_dict(h, derived=True)
    d["uid"] = h.uid
    d["rack_uid"] = h.rack.uid if h.rack else ""
    p = g.principal
    d["issues"] = [i for i in lint.run() if i["kind"] == "host" and i["ref"] == h.name]
    d["can_edit"] = p.can("host", "edit", rbac.host_facts(d))
    from .. import features
    d["features_expanded"] = features.expanded(h.features or [])
    d["merged_vars"] = features.host_vars(h)
    d["guests"] = [n for n in d["guests"] if p.can("host", "view", rbac.host_facts(store.host_to_dict(_host(n))))]
    from .. import discovery, health
    srcs = [x for x in health.sources().get(h.name, []) if p.can("integration", "view", rbac.named_facts(x["integration"]))]
    disc = discovery.live_status().get(h.name)
    d["health"] = {"live": health.combine(srcs, disc), "sources": srcs, "discovery": disc}
    return d


@bp.get("/hosts/<name>")
def get_host(name):
    h = _host(name)
    if h is None or not g.principal.can("host", "view", rbac.host_facts(store.host_to_dict(h))):
        return err(404, f"no host '{name}'")
    return jsonify(_host_detail(h))


@bp.put("/hosts/<name>")
def update_host(name):
    h = _host(name)
    if h is None:
        return err(404, f"no host '{name}'")
    before = store.host_to_dict(h)
    data = body()
    rbac.require("host", "edit", rbac.host_facts(before))
    rbac.require("host", "edit", _payload_facts(data))
    store.save_host(data, existing=h)
    db.session.flush()
    from .. import hostmodules
    hostmodules.rename_host(before["name"], h.name)
    audit.record("update", "host", h.name, before, store.host_to_dict(h))
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify(_host_detail(h))


@bp.delete("/hosts/<name>")
def delete_host(name):
    h = _host(name)
    if h is None:
        return err(404, f"no host '{name}'")
    before = store.host_to_dict(h)
    rbac.require("host", "edit", rbac.host_facts(before))
    db.session.delete(h)
    from .. import hostmodules
    hostmodules.delete_host(before["name"])
    audit.record("delete", "host", before["name"], before, None)
    db.session.commit()
    jobs.emit("host.deleted")
    return "", 204


@bp.post("/hosts/bulk")
def bulk_hosts():
    """One change to many hosts, in one transaction: all of them or none.
    {"names": [...], "status": "active", "runs_on": "srv-pm-pve-01" | "", "add_tags": [...],
     "remove_tags": [...], "module": {"integration": "pve", "enabled": true|false|null}, "delete": true}
    Each host gets its own history entry, all with the same summary."""
    from .. import hostmodules
    from ..models import HostModule, Integration
    data = body()
    names = [str(n) for n in data.get("names") or []]
    if not names:
        return err(422, "validation failed", [{"field": "names", "message": "pick at least one host"}])
    if len(names) > 1000:
        return err(422, "validation failed", [{"field": "names", "message": "at most 1000 hosts at once"}])
    hosts = []
    for n in dict.fromkeys(names):
        h = _host(n)
        if h is None:
            return err(404, f"no host '{n}'")
        before = store.host_to_dict(h)
        rbac.require("host", "edit", rbac.host_facts(before))
        hosts.append((h, before))
    add = [str(t).strip().lower() for t in data.get("add_tags") or [] if str(t).strip()]
    drop = {str(t).strip().lower() for t in data.get("remove_tags") or []}
    what = []
    if data.get("delete"):
        what.append("deleted")
    if "status" in data:
        what.append(f"status {data['status']}")
    if "runs_on" in data:
        what.append(f"runs on {data['runs_on'] or 'nothing'}")
    if add:
        what.append("tags +" + " +".join(add))
    if drop:
        what.append("tags −" + " −".join(sorted(drop)))
    mod = data.get("module") or None
    integ = None
    if mod:
        integ = db.session.scalar(db.select(Integration).where(Integration.name == str(mod.get("integration", ""))))
        if integ is None or not g.principal.can("integration", "view", rbac.named_facts(integ.name)):
            return err(404, f"no integration '{mod.get('integration')}'")
        if mod.get("enabled") not in (True, False, None):
            return err(422, "validation failed", [{"field": "module.enabled", "message": "true, false or null"}])
        what.append(f"{integ.name} {'on' if mod['enabled'] else 'off' if mod['enabled'] is False else 'as its selectors say'}")
    if not what:
        return err(422, "validation failed", [{"field": "names", "message": "nothing to change"}])
    summary = f"bulk ({len(hosts)} hosts): " + ", ".join(what)
    errors = []
    for h, before in hosts:
        if data.get("delete"):
            db.session.delete(h)
            hostmodules.delete_host(before["name"])
            audit.record("delete", "host", before["name"], before, None, summary=summary)
            continue
        changes = {}
        if "status" in data:
            changes["status"] = data["status"]
        if "runs_on" in data:
            changes["runs_on"] = data["runs_on"] or ""
        if add or drop:
            changes["tags"] = [t for t in dict.fromkeys([*before["tags"], *add]) if t not in drop]
        if changes:
            try:
                store.save_host({**before, **changes}, existing=h)
            except store.ValidationError as exc:
                errors += [{"field": f"{before['name']}.{e['field']}", "message": e["message"]} for e in exc.errors]
                continue
            rbac.require("host", "edit", _payload_facts({**before, **changes}))
        if integ is not None:
            hm = hostmodules.override(integ, h.name)
            if hm is None and mod["enabled"] is not None:
                hm = HostModule(integration_id=integ.id, host_name=h.name, enabled=None, settings={})
                db.session.add(hm)
            if hm is not None:
                hm.enabled = mod["enabled"]
                if hm.enabled is None and not hm.settings:
                    db.session.delete(hm)
        db.session.flush()
        audit.record("update", "host", h.name, before, store.host_to_dict(h), summary=summary)
    if errors:
        db.session.rollback()
        return err(422, "nothing changed: some hosts don't accept this", errors)
    db.session.commit()
    jobs.emit("host.deleted" if data.get("delete") else "host.updated")
    return jsonify({"changed": len(hosts), "summary": summary})


@bp.post("/preview/host")
def preview_host():
    data = request.get_json(silent=True) or {}
    out = {"name_ok": bool(naming.hostname_re().match(str(data.get("name", "")))),
           "zone_long": naming.zone_long(str(data.get("zone", ""))),
           "zone_colour": naming.zone_colour(str(data.get("zone", "")))}
    probe = Host(name=str(data.get("name", "")).lower(), zone=str(data.get("zone", "")).upper())
    out["aliases"] = {af or "dual": (n, len(naming.firewall_id(n)), naming.firewall_id(n))
                      for af in ("4", "6", "") for n in [naming.host_alias(probe, af)]}
    out["alias_max"] = naming.alias_max()
    nets = [i.get("network") for i in data.get("interfaces") or [] if i.get("network")]
    by_name = {n.name: n for n in db.session.scalars(db.select(Network).where(Network.name.in_(nets)))}
    out["dns"] = [f"{probe.name}.{naming.network_dns_parent(by_name[n])}" for n in nets if n in by_name]
    # IPv6 addresses each interface would get (token × the network's IPv6 prefixes)
    from types import SimpleNamespace
    from ..models import ip6_plan
    out["ip6"] = []
    for i in data.get("interfaces") or []:
        net = by_name.get(i.get("network"))
        try:
            plan = ip6_plan(SimpleNamespace(network=net, ip6=str(i.get("ip6") or ""), ip6_token=str(i.get("ip6_token") or ""),
                                            mac=str(i.get("mac") or ""), ip6_overrides=i.get("ip6_overrides") or {}))
        except ValueError:
            plan = []
        out["ip6"].append([{"address": a, "prefix": k} for a, k in plan])
    return jsonify(out)


# -------------------------------------------------------------------- aliases
@bp.get("/aliases")
def list_aliases():
    p = g.principal
    out = []
    for a in galiases.all_aliases():
        f = rbac.alias_facts(a.name)
        if p.can("alias", "view", f):
            d = a.as_dict()
            d["can_edit"] = a.kind == "custom" and p.can("alias", "edit", f)
            out.append(d)
    return jsonify(out)


@bp.get("/aliases/custom/<name>")
def get_custom_alias(name):
    a = db.session.scalar(db.select(AliasGroup).where(AliasGroup.name == name))
    if a is None or not g.principal.can("alias", "view", rbac.alias_facts(name)):
        return err(404, f"no custom alias '{name}'")
    return jsonify(store.alias_to_dict(a))


@bp.post("/aliases")
def create_alias():
    data = body()
    rbac.require("alias", "edit", rbac.alias_facts(str(data.get("name", ""))))
    a = store.save_alias(data)
    db.session.flush()
    audit.record("create", "alias", a.name, None, store.alias_to_dict(a))
    db.session.commit()
    jobs.emit("alias.created")
    return jsonify(store.alias_to_dict(a)), 201


@bp.put("/aliases/<name>")
def update_alias(name):
    a = db.session.scalar(db.select(AliasGroup).where(AliasGroup.name == name))
    if a is None:
        return err(404, f"no custom alias '{name}' (generated aliases can't be edited)")
    data = body()
    rbac.require("alias", "edit", rbac.alias_facts(name))
    rbac.require("alias", "edit", rbac.alias_facts(str(data.get("name", ""))))
    before = store.alias_to_dict(a)
    store.save_alias(data, existing=a)
    db.session.flush()
    audit.record("update", "alias", a.name, before, store.alias_to_dict(a))
    db.session.commit()
    jobs.emit("alias.updated")
    return jsonify(store.alias_to_dict(a))


@bp.delete("/aliases/<name>")
def delete_alias(name):
    a = db.session.scalar(db.select(AliasGroup).where(AliasGroup.name == name))
    if a is None:
        return err(404, f"no custom alias '{name}'")
    rbac.require("alias", "edit", rbac.alias_facts(name))
    before = store.alias_to_dict(a)
    db.session.delete(a)
    audit.record("delete", "alias", name, before, None)
    db.session.commit()
    jobs.emit("alias.deleted")
    return "", 204


# --------------------------------------------------------------- lint/services
def visible_issues(p) -> list[dict]:
    all_issues = lint.run()
    if p.is_admin:
        return list(all_issues)
    issues = []
    hosts = {h.name: h for h in db.session.scalars(db.select(Host))}
    nets = {n.name: n for n in db.session.scalars(db.select(Network))}
    seen: dict = {}
    for i in all_issues:
        key = (i["kind"], i["ref"])
        if key not in seen:
            if i["kind"] == "host":
                h = hosts.get(i["ref"])
                seen[key] = h is not None and p.can("host", "view", rbac.host_facts(store.host_to_dict(h)))
            elif i["kind"] == "network":
                n = nets.get(i["ref"])
                seen[key] = n is not None and p.can("network", "view", rbac.network_facts(store.network_to_dict(n)))
            elif i["kind"] == "feature":
                seen[key] = p.can_any("feature", "view") or p.can_any("host", "view")
            else:
                seen[key] = p.can("alias", "view", rbac.alias_facts(i["ref"]))
        if seen[key]:
            issues.append(i)
    return issues


@bp.get("/lint")
def get_lint():
    issues = visible_issues(g.principal)
    return jsonify({"summary": lint.summary(issues), "issues": issues})


@bp.get("/services")
def services():
    """Every service of every visible host, with where it listens and what Discovery saw there."""
    from ..models import DiscoveredService
    p = g.principal
    # what scans saw: open, or checked and closed (nmap probes every declared port, so a port it
    # never looked at stays unknown instead of "not answering")
    seen = {(d.ip, d.port, d.proto): d.state for d in db.session.scalars(db.select(DiscoveredService))}
    # monitors (e.g. Uptime Kuma) say more than a scan: {host: {service: (state, source)}}
    from .. import health
    monitored: dict[str, dict] = {}
    for hname, srcs in health.sources().items():
        for x in srcs:
            for sname, st in ((x.get("data") or {}).get("services") or {}).items():
                if x["state"] != "unknown" or st in ("up", "down"):
                    prev = monitored.setdefault(hname, {}).get(sname)
                    if prev is None or st == "down":
                        monitored[hname][sname] = (st, x["integration"])
    out = []
    for h in db.session.scalars(db.select(Host).where(Host.status != "offline").order_by(Host.name)):
        if not p.can("host", "view", rbac.host_facts(store.host_to_dict(h))):
            continue
        for s in h.services:
            addrs = store.service_addresses(h, s)
            proto = s.proto or "tcp"
            states = [seen.get((a, s.port, proto)) for a in addrs] if s.port else []
            status = "open" if "open" in states else "closed" if "closed" in states else "unknown"
            status_by = "discovery" if status != "unknown" else ""
            mon = monitored.get(h.name, {}).get(s.name)
            if mon and mon[0] in ("up", "down"):
                status, status_by = ("open" if mon[0] == "up" else "closed"), mon[1]
            out.append({"host": h.name, "host_uid": h.uid, "fun": h.fun or "", "zone": h.zone, "function": h.function, "kind": h.kind, "tags": list(h.tags or []),
                        "category": h.name.split("-")[0] if naming.hostname_re().match(h.name) else "",
                        "name": s.name, "port": s.port, "proto": s.proto, "url": store.service_url(h, s),
                        "description": s.description or "", "bind": s.bind or "", "addresses": addrs, "status": status, "status_by": status_by,
                        "allow_from": list(s.allow_from or []),
                        "srv": f"_{s.name}._{s.proto}.{naming.host_dns_zone(h)}" if s.port and s.publish_srv else ""})
    return jsonify(out)


@bp.get("/services/sources")
def service_sources():
    """What 'allow from' can name: zones (and area wildcards), networks, firewall aliases, any."""
    from ..generators import aliases as ga
    zones = naming.all_zones()
    areas = sorted({z.split("_")[0] + "_*" for z in zones})
    nets = [n.name for n in db.session.scalars(db.select(Network).order_by(Network.name))]
    custom = [a.name for a in db.session.scalars(db.select(AliasGroup).order_by(AliasGroup.name))]
    generated = sorted({a.name for a in ga.all_aliases()} - set(custom))
    from ..models import NetworkPrefix
    prefixes = [{"cidr": p.prefix, "network": p.network.name} for p in db.session.scalars(db.select(NetworkPrefix)) if p.prefix]
    return jsonify({"zones": zones, "areas": areas, "networks": nets, "aliases": custom, "generated": generated,
                    "prefixes": sorted(prefixes, key=lambda x: (x["network"], x["cidr"])), "special": ["any"]})


# keep the import used for type checkers
_ = inventory
