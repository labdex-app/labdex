"""Address management for one network: who uses what, and what's free.

The single-prefix functions (stats, free, contains) work on the network's first IPv4 prefix;
prefix_usage() covers every prefix of both families."""
from __future__ import annotations

import ipaddress

from .models import Interface, Network, db

# Networks larger than this are not enumerated address by address.
MAX_ENUMERATE = 4096


def _net(network: Network):
    try:
        return ipaddress.ip_network(network.prefix4, strict=False) if network.prefix4 else None
    except ValueError:
        return None


def _addr(text: str):
    try:
        return ipaddress.ip_address(text) if text else None
    except ValueError:
        return None


def dhcp_range(network: Network):
    start, end = _addr(network.dhcp_start), _addr(network.dhcp_end)
    return (start, end) if start and end and start <= end else None


def used(network: Network) -> dict[str, dict]:
    """ip -> {host, fun, label} for every address in this network (both families), gateways included."""
    out = {}
    gateways = {p.gateway for p in network.prefixes if p.gateway and _addr(p.gateway) is not None}
    for g in gateways:
        out[g] = {"host": None, "fun": None, "label": "gateway"}
    iface: Interface
    for iface in network.interfaces:
        for a in iface.all_addresses:
            if a:
                label = iface.label or ("gateway" if a in gateways else "")
                out[a] = {"host": iface.host.name, "fun": iface.host.fun, "label": label}
    return out


def _v4_used(network: Network) -> dict:
    return {a: v for a, v in used(network).items() if ":" not in a}


def stats(network: Network) -> dict:
    net = _net(network)
    if net is None:
        return {"size": 0, "used": len(_v4_used(network)), "free": 0, "percent": 0, "dhcp_size": 0}
    size = max(net.num_addresses - 2, 1) if net.prefixlen < 31 else net.num_addresses
    rng = dhcp_range(network)
    dhcp_size = int(rng[1]) - int(rng[0]) + 1 if rng else 0
    in_use = len(_v4_used(network))
    static_size = max(size - dhcp_size, 1)
    return {
        "size": size,
        "used": in_use,
        "dhcp_size": dhcp_size,
        "free": max(static_size - in_use, 0),
        "percent": round(100 * in_use / static_size, 1),
    }


def seen(net) -> set[str]:
    """Addresses in this prefix that Discovery saw (scans, ARP/NDP, DHCP leases, DNS) but no
    host has: free on paper, taken in practice."""
    from .models import DiscoveredHost
    out = set()
    for ip in db.session.scalars(db.select(DiscoveredHost.ip)):
        try:
            if ipaddress.ip_address(ip) in net:
                out.add(str(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    return out


def free(network: Network, limit: int = 10) -> list[str]:
    """Lowest free static addresses (outside the DHCP range, not the gateway)."""
    net = _net(network)
    if net is None or net.num_addresses > MAX_ENUMERATE * 16:
        return []
    taken = set(used(network)) | seen(net)
    rng = dhcp_range(network)
    out = []
    for addr in net.hosts():
        if rng and rng[0] <= addr <= rng[1]:
            continue
        if str(addr) in taken:
            continue
        out.append(str(addr))
        if len(out) >= limit:
            break
    return out


def known_prefixes(network: Network, family: int | None = None) -> list:
    """ip_network objects of every prefix with a known value (static, or delegated and seen)."""
    out = []
    for p in network.prefixes:
        if family and p.family != family or not p.prefix:
            continue
        try:
            out.append(ipaddress.ip_network(p.prefix, strict=False))
        except ValueError:
            pass
    return out


def contains(network: Network, ip: str) -> bool | None:
    """True/False, or None when the network has no prefix of that family to check against
    (or only delegated ones that aren't known yet)."""
    addr = _addr(ip)
    if addr is None:
        return None
    nets = known_prefixes(network, addr.version)
    has_delegated = any(p.family == addr.version and p.source == "delegated" for p in network.prefixes)
    if not nets:
        return None
    if any(addr in n for n in nets):
        return True
    return None if has_delegated else False


def prefix_usage(network: Network, free_limit: int = 12) -> list[dict]:
    """Per prefix: what it is, addresses in use, and for IPv4 the free ones."""
    out = []
    for p in network.prefixes:
        d = {"family": p.family, "prefix": p.prefix, "source": p.source, "description": p.description,
             "gateway": p.gateway, "dhcp_start": p.dhcp_start, "dhcp_end": p.dhcp_end, "used": 0, "size": 0, "free": [],
             "dhcp_size": 0, "percent": 0}
        try:
            net = ipaddress.ip_network(p.prefix, strict=False) if p.prefix else None
        except ValueError:
            net = None
        if net is not None:
            in_use = {a for i in network.interfaces for a in i.all_addresses if _addr(a) in net}
            if p.gateway and _addr(p.gateway) is not None and _addr(p.gateway) in net:
                in_use.add(p.gateway)
            d["used"] = len(in_use)
            if net.version == 4:
                size = max(net.num_addresses - 2, 1) if net.prefixlen < 31 else net.num_addresses
                s, e = _addr(p.dhcp_start), _addr(p.dhcp_end)
                dhcp = int(e) - int(s) + 1 if s and e and s <= e else 0
                static = max(size - dhcp, 1)
                d.update(size=size, dhcp_size=dhcp, percent=round(100 * len(in_use) / static, 1))
                if net.num_addresses <= MAX_ENUMERATE * 16:
                    for a in net.hosts():
                        if s and e and s <= a <= e or str(a) in in_use:
                            continue
                        d["free"].append(str(a))
                        if len(d["free"]) >= free_limit:
                            break
            if net.version == 6:
                d["free_tokens"] = free_tokens(network, net, limit=free_limit)
        out.append(d)
    return out


def free_tokens(network: Network, net, limit: int = 12) -> list[str]:
    """The lowest interface IDs (::1, ::2, …) nobody in this network uses yet, in any of its IPv6 prefixes."""
    from .models import token_value
    taken = set()
    for i in network.interfaces:
        t = token_value(i.ip6_token, i.mac)
        if t is not None:
            taken.add(t)
        for a in i.ip6s:
            for p in known_prefixes(network, 6):
                ad = ipaddress.ip_address(a)
                if ad in p:
                    taken.add(int(ad) - int(p.network_address))
    for p in known_prefixes(network, 6):
        for a in seen(p):
            taken.add(int(ipaddress.ip_address(a)) - int(p.network_address))
    for p in network.prefixes:
        if p.family == 6 and p.gateway:
            g = _addr(p.gateway)
            if g is not None:
                taken.add(int(g) - int(net.network_address) if g in net else int(g))
    out, n = [], 1
    while len(out) < limit and n < 1 << 16:
        if n not in taken:
            out.append(str(ipaddress.IPv6Address(n)))
        n += 1
    return out
