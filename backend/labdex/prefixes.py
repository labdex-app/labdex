"""Delegated IPv6 prefixes: turn what a firewall received on its WAN into each network's prefix.

A network prefix with source 'delegated' says "the /<length> number <prefix_id> of whatever the firewall
gets on <delegated_from>". When the ISP hands out a new prefix, apply_observed() moves those networks to
it, records the change in the history and announces it, so DNS and firewall aliases follow.
"""
from __future__ import annotations

import ipaddress

from . import audit, store
from .models import NetworkPrefix, db


def derive(delegated: str, prefix_id: int, length: int) -> str | None:
    """Subnet <prefix_id> of size /<length> inside the delegated prefix, or None if it doesn't fit."""
    try:
        pd = ipaddress.IPv6Network(delegated, strict=False)
    except ValueError:
        return None
    if length < pd.prefixlen or prefix_id < 0 or prefix_id >= 1 << (length - pd.prefixlen):
        return None
    return str(ipaddress.IPv6Network((int(pd.network_address) + (prefix_id << (128 - length)), length)))


def apply_observed(observed: dict[str, str], source: str) -> dict:
    """observed: WAN interface name → delegated prefix (e.g. {"wan": "2001:db8:ab00::/56"}).
    Returns {"changed": [{network, from, to}], "unchanged": n, "problems": [...]}."""
    observed = {k.lower(): v for k, v in observed.items() if v}
    changed, problems, unchanged = [], [], 0
    by_net: dict[int, tuple] = {}
    for p in db.session.scalars(db.select(NetworkPrefix).where(NetworkPrefix.source == "delegated")):
        pd = observed.get((p.delegated_from or "wan").lower())
        if pd is None:
            problems.append(f"{p.network.name}: nothing received on '{p.delegated_from}'")
            continue
        new = derive(pd, p.prefix_id or 0, p.length or 64)
        if new is None:
            problems.append(f"{p.network.name}: prefix ID {p.prefix_id:#x} doesn't fit /{p.length} inside {pd}")
            continue
        if new == p.prefix:
            unchanged += 1
            continue
        if p.network_id not in by_net:
            by_net[p.network_id] = (p.network, store.network_to_dict(p.network))
        changed.append({"network": p.network.name, "from": p.prefix or None, "to": new, "delegated": pd})
        p.prefix = new
    if changed:
        db.session.flush()
        for net, before in by_net.values():
            moves = [c for c in changed if c["network"] == net.name]
            audit.record("update", "network", net.name, before, store.network_to_dict(net),
                         summary="delegated prefix " + ", ".join(f"{c['from'] or 'unknown'} → {c['to']}" for c in moves) + f" ({source})")
        db.session.commit()
        from .plugins import jobs
        jobs.emit("network.updated")
    return {"changed": changed, "unchanged": unchanged, "problems": problems}
