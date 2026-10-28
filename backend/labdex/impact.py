"""What breaks if these hosts go down: for a failure, or before planned maintenance.

From what the inventory knows, nothing is guessed:

    down        the hosts themselves and what runs on them (VMs, containers), recursively
    cut off     hosts whose every cable path to their network's gateway runs through
                something that's down (patch panels followed port by port: two runs through
                one panel stay apart); a VM is where its hypervisor is
    no routing  networks whose gateways are all down or cut off: the hosts in them still
                reach each other, nothing beyond
    vips        a VIP is down when every member is affected, degraded when some are
    services    every service on an affected host; dns: are all or some DNS servers affected
    unknown     hosts with no cable on record and nothing they run on: can't tell

Every affected host comes with why ("runs on srv-pm-pve-01", "only cabled through
net-sw-core-01"), so the answer can be checked.
"""
from __future__ import annotations

from collections import defaultdict, deque

from .models import Host, Link, Network, db

DNS_PORTS = {53}


def _graph(hosts: dict[int, Host]):
    """Nodes: hosts, and patch panel port pairs (a panel isn't one node: its runs are separate).
    Edges: cables (not 'virtual' ones), and guest → host (a VM is where its hypervisor is)."""
    edges: dict = defaultdict(set)
    passthrough: dict[int, dict[str, str]] = {}
    for h in hosts.values():
        pairs = {(i.label or "").lower(): (i.passthrough or "").lower() for i in h.interfaces if i.passthrough}
        if pairs:
            passthrough[h.id] = pairs

    def node(host_id: int, port: str):
        pairs = passthrough.get(host_id)
        p = (port or "").lower()
        if pairs and (p in pairs or p in pairs.values()):
            other = pairs.get(p) or next((k for k, v in pairs.items() if v == p), "")
            return ("pp", host_id, tuple(sorted((p, other))))
        return ("h", host_id)

    for lk in db.session.scalars(db.select(Link)):
        if lk.medium == "virtual" or lk.a_host_id not in hosts or lk.b_host_id not in hosts:
            continue
        a, b = node(lk.a_host_id, lk.a_port), node(lk.b_host_id, lk.b_port)
        if a != b:
            edges[a].add(b)
            edges[b].add(a)
    for h in hosts.values():
        if h.runs_on_id and h.runs_on_id in hosts:
            a, b = ("h", h.id), ("h", h.runs_on_id)
            edges[a].add(b)
            edges[b].add(a)
    return edges


def _reach(edges, starts, blocked) -> set:
    seen, todo = set(), deque(s for s in starts if s not in blocked)
    seen.update(todo)
    while todo:
        n = todo.popleft()
        for m in edges.get(n, ()):
            if m not in seen and m not in blocked:
                seen.add(m)
                todo.append(m)
    return seen


def _host_of(n) -> int:
    return n[1]


def analyse(names: list[str], visible=None) -> dict:
    """The impact of the hosts called `names` going down. `visible(host) -> bool` limits what's
    listed (everything is still taken into account: hidden hosts count, they just aren't named)."""
    hosts = {h.id: h for h in db.session.scalars(db.select(Host))}
    by_name = {h.name: h for h in hosts.values()}
    failed = [by_name[n] for n in dict.fromkeys(names) if n in by_name]
    show = visible or (lambda h: True)
    why: dict[int, str] = {}
    down: set[int] = set()

    # down: the hosts, and what runs on them
    guests = defaultdict(list)
    for h in hosts.values():
        if h.runs_on_id:
            guests[h.runs_on_id].append(h)
    todo = deque()
    for h in failed:
        down.add(h.id)
        why[h.id] = "chosen"
        todo.append(h)
    while todo:
        h = todo.popleft()
        for gst in guests.get(h.id, ()):
            if gst.id not in down:
                down.add(gst.id)
                why[gst.id] = f"runs on {h.name}"
                todo.append(gst)

    # the gateways: hosts (or a VIP's members) holding a network's gateway address
    by_ip: dict[str, Host] = {}
    for h in hosts.values():
        for i in h.interfaces:
            if i.ip4:
                by_ip.setdefault(i.ip4, h)
    gateways: dict[int, set[int]] = {}
    nets = {n.id: n for n in db.session.scalars(db.select(Network))}
    for n in nets.values():
        g = by_ip.get(n.gateway4 or "")
        if g is None:
            continue
        members = [m.id for m in g.vip_members] if g.kind == "vip" and g.vip_members else [g.id]
        gateways[n.id] = set(members)

    edges = _graph(hosts)
    blocked = {n for n in edges if n[0] == "h" and _host_of(n) in down} | \
              {("h", d) for d in down} | {n for n in edges if n[0] == "pp" and _host_of(n) in down}
    cabled = {_host_of(n) for n in edges if n[0] == "h"}

    # the roots: every gateway that isn't down. Reachable from one = still connected.
    all_gws = {g for gs in gateways.values() for g in gs}
    alive_gws = {g for g in all_gws if g not in down}
    reach = _reach(edges, [("h", g) for g in alive_gws], blocked)
    # what the gateways reached before: only what was connected then can be cut off now
    before = _reach(edges, [("h", g) for g in all_gws], set())

    cut: set[int] = set()
    if all_gws:
        for hid in cabled:
            if hid in down or hid in all_gws and hid in alive_gws and ("h", hid) in reach:
                continue
            if ("h", hid) in before and ("h", hid) not in reach:
                cut.add(hid)
                # the down neighbours of its remaining island: what it hung on
                island = _reach(edges, [("h", hid)], blocked)
                on = sorted({hosts[_host_of(m)].name for n in island for m in edges.get(n, ()) if m in blocked})
                why[hid] = ("only cabled through " + ", ".join(on)) if on else "no path to a gateway any more"
    gone = down | cut

    # networks without a working gateway
    no_routing = []
    for nid, gws in sorted(gateways.items(), key=lambda x: nets[x[0]].name):
        if gws and gws <= gone:
            n = nets[nid]
            members = sorted({i.host.name for i in n.interfaces if i.host_id not in gone and show(i.host)})
            gw_names = sorted(hosts[g].name for g in gws)
            no_routing.append({"network": n.name, "uid": n.uid, "zone": n.zone, "gateway": n.gateway4,
                               "because": f"its gateway ({', '.join(gw_names)}) is {'down' if gws <= down else 'cut off'}",
                               "hosts": members})

    def row(hid: int) -> dict:
        h = hosts[hid]
        return {"host": h.name, "uid": h.uid, "fun": h.fun or "", "zone": h.zone, "function": h.function or "",
                "because": why.get(hid, ""), "status": h.status}

    affected = sorted(gone, key=lambda i: hosts[i].name)
    vips = []
    for h in sorted((h for h in hosts.values() if h.kind == "vip" and h.vip_members), key=lambda h: h.name):
        hit = [m.name for m in h.vip_members if m.id in gone]
        if hit and show(h):
            state = "down" if len(hit) == len(h.vip_members) else "degraded"
            vips.append({"host": h.name, "uid": h.uid, "state": state, "members_affected": hit,
                         "members_left": [m.name for m in h.vip_members if m.id not in gone]})
    services = []
    for hid in affected:
        h = hosts[hid]
        if not show(h):
            continue
        for s in h.services:
            from .store import service_url
            services.append({"host": h.name, "uid": h.uid, "name": s.name, "port": s.port, "proto": s.proto or "tcp",
                             "url": service_url(h, s), "description": s.description or ""})
    for v in vips:  # a VIP that's down takes its services with it
        if v["state"] == "down":
            h = by_name[v["host"]]
            from .store import service_url
            services += [{"host": h.name, "uid": h.uid, "name": s.name, "port": s.port, "proto": s.proto or "tcp",
                          "url": service_url(h, s), "description": s.description or ""} for s in h.services]
    dns_servers = [h for h in hosts.values() if h.status != "offline" and h.kind != "vip"
                   and any(s.port in DNS_PORTS for s in h.services)]
    dns_hit = [h.name for h in dns_servers if h.id in gone]
    dns = None
    if dns_hit:
        dns = {"state": "down" if len(dns_hit) == len(dns_servers) else "degraded", "affected": sorted(dns_hit),
               "left": sorted(h.name for h in dns_servers if h.id not in gone)}
    unknown = sorted(h.name for h in hosts.values() if h.id not in gone and h.id not in cabled and not h.runs_on_id
                     and h.kind != "vip" and h.status not in ("offline", "planned") and show(h))
    hidden = sum(1 for i in affected if not show(hosts[i]))
    return {
        "failed": [h.name for h in failed],
        "unknown_names": [n for n in names if n not in by_name],
        "down": [row(i) for i in affected if i in down and show(hosts[i])],
        "cut_off": [row(i) for i in affected if i in cut and show(hosts[i])],
        "no_routing": no_routing,
        "vips": vips,
        "services": services,
        "dns": dns,
        "unknown": unknown,
        "hidden": hidden,
        "gateways_known": bool(all_gws),
        "counts": {"down": len(down), "cut_off": len(cut), "no_routing": len(no_routing), "services": len(services),
                   "vips_down": sum(1 for v in vips if v["state"] == "down")},
    }
