"""Convention checks over the whole inventory.

These don't block saving (real networks are messy during a migration); they
show up on the Lint page and in `labdex lint`, which exits non-zero on errors
so it can gate CI.
"""
from __future__ import annotations

import ipaddress
import json
from collections import defaultdict

from . import ipam, naming
from .generators import aliases as galiases
from .generators import dns
from .models import Host, Network, db


def _issue(severity, code, kind, ref, message):
    return {"severity": severity, "code": code, "kind": kind, "ref": ref, "message": message}


def accepted() -> list[dict]:
    """Findings someone accepted on purpose: [{code, kind, ref, reason}]. Kept in the `lint_accepted` setting."""
    try:
        rows = json.loads(naming.get_setting("lint_accepted") or "[]")
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("code") and r.get("kind") and r.get("ref")]


def run_all() -> list[dict]:
    """Every finding; accepted ones carry `accepted` (the reason)."""
    from .cache import memo
    return memo("lint", _run)


def run() -> list[dict]:
    """The findings that still count: what `labdex lint`, the dashboard and host pages show."""
    return [i for i in run_all() if "accepted" not in i]


def _run() -> list[dict]:
    out: list[dict] = []
    hosts = db.session.scalars(db.select(Host).order_by(Host.name)).all()
    by_name = {h.name: h for h in hosts}
    nets = db.session.scalars(db.select(Network).order_by(Network.name)).all()
    name_re = naming.hostname_re()
    octet_rule = naming.get_setting("octet_matches_vlan").lower() == "true"

    # ---------------------------------------------------------------- networks
    for n in nets:
        if octet_rule and n.vlan and n.prefix4:
            net = ipaddress.ip_network(n.prefix4)
            if 16 <= net.prefixlen <= 24 and net.network_address.packed[2] != n.vlan:
                out.append(_issue("warning", "octet-vlan", "network", n.name,
                                  f"third octet {net.network_address.packed[2]} doesn't match VLAN {n.vlan}"))
        if n.vlan and not n.prefixes:
            out.append(_issue("warning", "no-prefix", "network", n.name, "VLAN without an address prefix"))
        out.extend(_prefix_checks(n))
        st = ipam.stats(n)
        if st["size"] and st["percent"] >= 90:
            out.append(_issue("warning", "nearly-full", "network", n.name,
                              f"{st['percent']} % of static addresses used"))

    out.extend(_overlaps(nets))

    # ------------------------------------------------------------------- hosts
    seen_ip = defaultdict(list)
    seen_mac = defaultdict(list)
    for h in hosts:
        if h.status != "legacy" and not name_re.match(h.name):
            out.append(_issue("warning", "hostname", "host", h.name,
                              f"doesn't match <category>-<type>-<role>-<nn> (categories: {', '.join(naming.categories())})"))
        if h.status in ("active", "planned") and not h.interfaces:
            out.append(_issue("info", "no-interfaces", "host", h.name, "no interfaces yet"))
        rank = naming.trust_rank(h.zone)
        for i in h.interfaces:
            if i.parent and ":" in i.parent:
                ph, _, pport = i.parent.partition(":")
                other = by_name.get(ph)
                if other is None or not any(x.label == pport for x in other.interfaces):
                    out.append(_issue("warning", "interface-parent", "host", h.name,
                                      f"{i.label or i.ip4 or 'an interface'} sits on {i.parent}, which doesn't exist"))
            n = i.network
            if n is not None:
                nrank = naming.trust_rank(n.zone)
                if rank is not None and nrank is not None and nrank > rank:
                    out.append(_issue("warning", "placement", "host", h.name,
                                      f"{h.zone} host sits in {n.name} ({n.zone}): above its own trust level"))
                rng = ipam.dhcp_range(n)
                if i.ip4 and rng and rng[0] <= ipaddress.ip_address(i.ip4) <= rng[1] and not i.mac:
                    out.append(_issue("warning", "in-dhcp-range", "host", h.name,
                                      f"{i.ip4} is inside the DHCP range of {n.name} without a MAC reservation"))
            if i.ip4:
                seen_ip[i.ip4].append(h.name)
            for a6 in i.ip6s:
                seen_ip[a6].append(h.name)
            if i.mac:
                seen_mac[i.mac].append(h.name)
        out.extend(_port_clashes(h))
        if h.runs_on is not None and h.runs_on.status == "offline" and h.status == "active":
            out.append(_issue("warning", "runs-on-offline", "host", h.name, f"active, but its host {h.runs_on.name} is offline"))

    for ip, owners in seen_ip.items():
        if len(owners) > 1:
            out.append(_issue("error", "duplicate-ip", "host", owners[0], f"{ip} is used by {', '.join(owners)}"))
    for mac, owners in seen_mac.items():
        if len(owners) > 1:
            out.append(_issue("error", "duplicate-mac", "host", owners[0], f"{mac} is used by {', '.join(owners)}"))

    # ----------------------------------------------------------------- aliases
    limit = naming.alias_max()
    seen_alias = defaultdict(list)
    for a in galiases.all_aliases():
        seen_alias[a.name].append(a.kind)
        if a.fw_id != a.name and a.kind != "custom":
            out.append(_issue("info", "alias-shortened", "alias", a.name,
                              f"{len(a.name)} characters: the firewall gets the id {a.fw_id} (limit {limit})"))
        for err in a.errors:
            out.append(_issue("error", "alias-member", "alias", a.name, err))
        if a.kind == "custom" and not a.content:
            out.append(_issue("warning", "alias-empty", "alias", a.name, "resolves to no entries"))
    for name, kinds in seen_alias.items():
        if len(kinds) > 1:
            out.append(_issue("error", "alias-duplicate", "alias", name, f"defined twice ({', '.join(kinds)})"))

    # ----------------------------------------------------- services: allow from
    from fnmatch import fnmatchcase
    known_nets = {n.name for n in db.session.scalars(db.select(Network))}
    known_zones = naming.all_zones()
    alias_names = {a.name for a in galiases.all_aliases()}
    for h in hosts:
        for s in h.services:
            for t in s.allow_from or []:
                if t.lower() == "any" or t in known_nets or t in alias_names or any(fnmatchcase(z, t.upper()) for z in known_zones):
                    continue
                try:
                    ipaddress.ip_network(t, strict=False)
                    continue
                except ValueError:
                    pass
                out.append(_issue("warning", "allow-from-unknown", "host", h.name,
                                  f"service {s.name}: 'allow from' {t} is no zone, network, alias or CIDR"))

    # --------------------------------------------------------- public services
    dmz = (naming.get_setting("dmz_area") or "DMZ").upper()
    for h in hosts:
        for s in h.services:
            if not s.public:
                continue
            if h.zone.split("_")[0] != dmz:
                out.append(_issue("warning", "public-outside-dmz", "host", h.name,
                                  f"service {s.name} is public, but the host isn't in the {dmz} area"))
            if not s.public_name:
                out.append(_issue("info", "public-without-name", "host", h.name,
                                  f"service {s.name} is public without a public name, so there's no split-horizon DNS for it"))

    # ------------------------------------------------- secrets in Ansible vars
    from . import ansiblevault, secretstore
    from .models import Feature
    for f in db.session.scalars(db.select(Feature).order_by(Feature.path)):
        for name in sorted(n for n in ansiblevault.refs(f.vars or {}) if not secretstore.exists(n)):
            out.append(_issue("error", "secret-missing", "feature", f.path, f"vars point at {secretstore.label(name)}, which isn't in the vault"))
    for h in hosts:
        for name in sorted(n for n in ansiblevault.refs(h.vars or {}) if not secretstore.exists(n)):
            out.append(_issue("error", "secret-missing", "host", h.name, f"vars point at {secretstore.label(name)}, which isn't in the vault"))

    # --------------------------------------------------------------------- DNS
    by_name = defaultdict(set)
    domain = naming.get_setting("domain")
    for r in dns.records():
        by_name[r.fqdn(domain)].add((r.type, r.host))  # names in different zones never clash
    for name, entries in by_name.items():
        types = {t for t, _ in entries}
        owners = sorted({o for _, o in entries})
        if "CNAME" in types and len(entries) > 1:
            out.append(_issue("error", "dns-conflict", "host", owners[0],
                              f"DNS name '{name}' is a CNAME and also used by {', '.join(owners)}"))

    reasons = {(a["code"], a["kind"], a["ref"]): a.get("reason", "") for a in accepted()}
    for i in out:
        key = (i["code"], i["kind"], i["ref"])
        if key in reasons:
            i["accepted"] = reasons[key]
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda i: (order[i["severity"]], i["kind"], i["ref"], i["code"]))


def _cidr(text: str):
    try:
        return ipaddress.ip_network(text, strict=False) if text else None
    except ValueError:
        return None


def _prefix_checks(n: Network) -> list[dict]:
    """Gateway and DHCP range against the prefix they belong to; IPv6 prefix length."""
    out = []
    for p in n.prefixes:
        net = _cidr(p.prefix) if p.source == "static" else None
        if net is None:
            continue
        if net.version == 6 and net.prefixlen != 64 and net.prefixlen < 127:
            out.append(_issue("info", "prefix6-length", "network", n.name,
                              f"{p.prefix} isn't a /64, which SLAAC and most clients expect"))
        if net.version != 4:
            continue  # delegated prefixes keep suffixes, IPv6 static ones rarely carry a pool
        gw = p.gateway
        if gw:
            try:
                if ipaddress.ip_address(gw) not in net:
                    out.append(_issue("warning", "gateway-outside", "network", n.name,
                                      f"gateway {gw} is outside {p.prefix}"))
            except ValueError:
                pass
        if p.dhcp_start and p.dhcp_end:
            try:
                a, b = ipaddress.ip_address(p.dhcp_start), ipaddress.ip_address(p.dhcp_end)
            except ValueError:
                continue
            if a > b:
                out.append(_issue("warning", "dhcp-range-order", "network", n.name,
                                  f"DHCP range starts at {p.dhcp_start}, after its end {p.dhcp_end}"))
            elif a not in net or b not in net:
                out.append(_issue("warning", "dhcp-range-outside", "network", n.name,
                                  f"DHCP range {p.dhcp_start}–{p.dhcp_end} reaches outside {p.prefix}"))
            elif gw and a <= ipaddress.ip_address(gw) <= b:
                out.append(_issue("warning", "dhcp-range-gateway", "network", n.name,
                                  f"the gateway {gw} is inside the DHCP range"))
    return out


def _overlaps(nets) -> list[dict]:
    """Two networks whose static prefixes overlap (reported on both)."""
    flat = []
    for n in nets:
        for p in n.prefixes:
            net = _cidr(p.prefix) if p.source == "static" else None
            if net is not None:
                flat.append((n.name, net))
    out = []
    seen = set()
    for i, (a_name, a) in enumerate(flat):
        for b_name, b in flat[i + 1:]:
            if a_name == b_name or a.version != b.version or not a.overlaps(b):
                continue
            for mine, other, o_net in ((a_name, b_name, b), (b_name, a_name, a)):
                key = (mine, other)
                if key not in seen:
                    seen.add(key)
                    out.append(_issue("error", "prefix-overlap", "network", mine, f"overlaps {other} ({o_net})"))
    return out


def _port_clashes(h: Host) -> list[dict]:
    """The same port and protocol twice on one host, listening on addresses that can coincide."""
    out = []
    by_port = defaultdict(list)
    for s in h.services:
        if s.port:
            by_port[(s.port, s.proto)].append(s)
    for (port, proto), svcs in by_port.items():
        for i, a in enumerate(svcs):
            for b in svcs[i + 1:]:
                if not a.bind or not b.bind or a.bind == b.bind:
                    out.append(_issue("warning", "service-port-clash", "host", h.name,
                                      f"services {a.name} and {b.name} both use {port}/{proto}"))
    return out


def summary(issues: list[dict]) -> dict:
    """Counts per severity; accepted findings don't count."""
    return {s: sum(1 for i in issues if i["severity"] == s and "accepted" not in i) for s in ("error", "warning", "info")}
