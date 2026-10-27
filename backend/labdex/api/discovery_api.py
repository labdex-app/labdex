from __future__ import annotations

import ipaddress
import re

from flask import abort, g, jsonify, request

from .. import audit, discovery, rbac, store
from ..models import DiscoveredHost, DiscoveredService, Host, Network, Service, db
from ..plugins import jobs
from . import body, bp, err

PER_PAGE = 50


def _need_view():
    if not g.principal.can_any("discovery", "view"):
        rbac.require("discovery", "view")


def _page(rows: list, search_keys=()) -> dict:
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [r for r in rows if any(q in str(r.get(k) or "").lower() for k in search_keys)]
    try:
        page = max(int(request.args.get("page", 1)), 1)
        per = min(max(int(request.args.get("per_page", PER_PAGE)), 1), 500)
    except ValueError:
        page, per = 1, PER_PAGE
    return {"items": rows[(page - 1) * per: page * per], "total": len(rows), "page": page, "per_page": per}


@bp.get("/discovery/summary")
def discovery_summary():
    _need_view()
    return jsonify(discovery.summary())


@bp.get("/discovery/services")
def discovery_services():
    """?status=undocumented,unknown-host (default: everything but documented) | all; ?q=; ?page=&per_page="""
    _need_view()
    rows = discovery.services(include_closed=request.args.get("closed") == "1")
    status = request.args.get("status") or "undocumented,unknown-host"
    if status != "all":
        wanted = set(status.split(","))
        rows = [r for r in rows if r["status"] in wanted]
    return jsonify(_page(rows, ("ip", "service", "product", "host", "fun", "network", "zone", "port")))


@bp.get("/discovery/missing")
def discovery_missing():
    _need_view()
    return jsonify(_page(discovery.missing(), ("host", "fun", "service", "port")))


@bp.get("/discovery/unknown-hosts")
def discovery_unknown():
    _need_view()
    return jsonify(_page(discovery.unknown_hosts(), ("ip", "mac", "hostname", "vendor", "network", "source")))


@bp.get("/discovery/macs")
def discovery_macs():
    """Interfaces without a MAC whose address Discovery saw with one (ARP, DHCP leases, nmap as root)."""
    _need_view()
    p = g.principal
    rows = [m | {"can_edit": p.can("host", "edit", rbac.host_facts(store.host_access(h)))}
            for m in discovery.mac_suggestions()
            if (h := discovery.store_host(m["host"])) is not None and p.can("host", "view", rbac.host_facts(store.host_access(h)))]
    return jsonify(_page(rows, ("host", "fun", "ip", "mac", "vendor", "network", "source")))


@bp.post("/discovery/macs/apply")
def discovery_macs_apply():
    """{"items": [{"host", "ip", "mac"}]}: write the MACs into those interfaces (one history entry per host)."""
    items = body().get("items") or []
    by_host: dict[str, list[dict]] = {}
    for it in items:
        by_host.setdefault(str(it.get("host", "")), []).append(it)
    done = 0
    for name, its in by_host.items():
        h, before = _load_host(name)
        changed = 0
        for it in its:
            mac = store.norm_mac(str(it.get("mac", "")))
            iface = next((i for i in h.interfaces if it.get("ip") in i.all_addresses), None)
            if not mac or iface is None or iface.mac:
                continue
            if h.is_vip:
                continue
            iface.mac = mac
            changed += 1
        if changed:
            db.session.flush()
            audit.record("update", "host", h.name, before, store.host_to_dict(h), summary=f"MAC from discovery ({changed})")
            done += changed
    db.session.commit()
    if done:
        jobs.emit("host.updated")
    return jsonify({"applied": done})


def _host_by_ip(ip: str) -> Host | None:
    return next((h for h in db.session.scalars(db.select(Host)) if any(ip in i.all_addresses for i in h.interfaces)), None)


def _service_name(raw: str, port: int) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(raw or f"port-{port}").lower()).strip("-")[:56] or f"port-{port}"


def _scheme(ds: DiscoveredService) -> str:
    if ds.service in ("https", "ssl/http") or ds.port in (443, 8443):
        return "https"
    return "http" if (ds.service or "").startswith("http") else ""


def _add_service(host: Host, ds: DiscoveredService, name: str) -> str:
    if any(s.port == ds.port and (s.proto or "tcp") == ds.proto for s in host.services):
        return ""  # already declared
    if any(s.name == name for s in host.services):
        name = f"{name}-{ds.port}"
    host.services.append(Service(name=name, port=ds.port, proto=ds.proto, scheme=_scheme(ds)))
    return name


def _load_host(name: str) -> tuple[Host, dict]:
    h = store.find_host(name)
    if h is None:
        abort(404, description=f"no host '{name}'")
    before = store.host_to_dict(h)
    rbac.require("host", "edit", rbac.host_facts(before))
    return h, before


def _ensure_interface(host: Host, ip: str, mac: str = "") -> bool:
    """Give the host an interface with this address (in the matching network). True if added."""
    if any(ip in i.all_addresses for i in host.interfaces):
        return False
    from ..models import Interface
    net_light = discovery.network_of(ip)
    net = db.session.scalar(db.select(Network).where(Network.name == net_light.name)) if net_light else None
    v6 = ipaddress.ip_address(ip).version == 6
    label = "" if net else f"if{len(host.interfaces)}"
    host.interfaces.append(Interface(network=net, label=label, ip4="" if v6 else ip, ip6=ip if v6 else "",
                                     mac=store.norm_mac(mac) if mac else "", primary=not host.interfaces))
    return True


@bp.post("/discovery/services/<int:sid>/adopt")
def adopt_service(sid):
    """Declare a discovered port as a service: on the host that has the address,
    or (for an unknown address) on the host given as {"host": …}, which gets an
    interface with that address too."""
    ds = db.session.get(DiscoveredService, sid)
    if ds is None:
        return err(404, "no such discovered service")
    data = request.get_json(silent=True) or {}
    owner = _host_by_ip(ds.ip)
    if owner is None and not data.get("host"):
        return err(409, f"{ds.ip} isn't in the inventory; pick a host or create one")
    if owner is not None and data.get("host") and data["host"] not in (owner.name, *owner.fun_names):
        return err(409, f"{ds.ip} belongs to {owner.name}")
    host, before = _load_host(owner.name if owner else str(data["host"]))
    added_if = _ensure_interface(host, ds.ip)
    name = _add_service(host, ds, _service_name(data.get("name") or ds.service, ds.port))
    db.session.flush()
    rbac.require("host", "edit", rbac.host_facts(store.host_access(host)))  # still allowed where it ends up
    what = ([f"interface {ds.ip}"] if added_if else []) + ([f"service {name} ({ds.port}/{ds.proto})"] if name else [])
    audit.record("update", "host", host.name, before, store.host_to_dict(host),
                 summary=f"from discovery: {', '.join(what) or 'nothing new'}")
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify(store.host_to_dict(host)), 201


@bp.post("/discovery/addresses/assign")
def assign_address():
    """{ip, host}: add a seen address (with its MAC) to an existing host, plus its open ports as services if asked."""
    data = body()
    ip = str(data.get("ip", ""))
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return err(422, "validation failed", [{"field": "ip", "message": "not an address"}])
    owner = _host_by_ip(ip)
    if owner is not None:
        return err(409, f"{ip} already belongs to {owner.name}")
    host, before = _load_host(str(data.get("host", "")))
    dh = db.session.scalar(db.select(DiscoveredHost).where(DiscoveredHost.ip == ip))
    _ensure_interface(host, ip, dh.mac if dh and not host.is_vip else "")  # a VIP has no MAC of its own
    if dh is not None and dh.vendor and not host.vendor and not host.is_vip:
        host.vendor = dh.vendor[:100]  # what the MAC's vendor says, until you know better
    added = []
    if data.get("with_services"):
        for ds in db.session.scalars(db.select(DiscoveredService).where(DiscoveredService.ip == ip, DiscoveredService.state == "open")):
            n = _add_service(host, ds, _service_name(ds.service, ds.port))
            if n:
                added.append(n)
    db.session.flush()
    rbac.require("host", "edit", rbac.host_facts(store.host_access(host)))
    audit.record("update", "host", host.name, before, store.host_to_dict(host),
                 summary=f"from discovery: interface {ip}" + (f", services {', '.join(added)}" if added else ""))
    db.session.commit()
    jobs.emit("host.updated")
    return jsonify(store.host_to_dict(host)), 201


@bp.delete("/discovery/addresses/<ip>")
def forget_address(ip):
    """Forget a seen address and its ports (e.g. a phone that left)."""
    _need_view()
    net = discovery.network_of(ip)
    rbac.require("discovery", "admin", rbac.Facts.make(ip, [net.zone] if net else [], [net.name] if net else []))
    db.session.execute(db.delete(DiscoveredService).where(DiscoveredService.ip == ip))
    db.session.execute(db.delete(DiscoveredHost).where(DiscoveredHost.ip == ip))
    db.session.commit()
    return "", 204


@bp.get("/discovery/where")
def discovery_where():
    """?q=<MAC, IP or host>: which switch port its MAC addresses were seen on (switch MAC tables)."""
    from .. import macs
    _need_view()
    p = g.principal
    visible = {e["host"]["name"] for e in store.host_index() if p.can("host", "view", e["facts"])}
    q = (request.args.get("q") or "").strip()
    if not q:
        return err(422, "validation failed", [{"field": "q", "message": "a MAC, an IP address or a host"}])
    return jsonify(macs.where(q, visible))
