"""Firewall aliases: generated ones from networks, zones and hosts, plus the
hand-made groups with their members resolved."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from .. import naming
from ..models import AliasGroup, Area, Host, Network, db


@dataclass
class Alias:
    name: str
    type: str
    content: list[str]
    description: str
    kind: str  # network | zone | area | host | custom
    source: str = ""
    errors: list[str] = field(default_factory=list)
    fw_id: str = ""  # name on the firewall (≤ 32 chars); set by all_aliases()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "fw_id": self.fw_id or self.name,
            "type": self.type,
            "content": self.content,
            "description": self.description,
            "kind": self.kind,
            "source": self.source,
            "length": len(self.fw_id or self.name),
            "errors": self.errors,
        }


def _live_hosts():
    return db.session.scalars(
        db.select(Host).where(Host.status != "offline").order_by(Host.name)
    ).all()


def network_aliases(net: Network) -> list[Alias]:
    out = []
    from .. import ipam
    for fam in ("4", "6"):
        prefixes = [str(p) for p in ipam.known_prefixes(net, int(fam))]
        if prefixes:  # every prefix of the family, e.g. ULA and GUA
            out.append(Alias(naming.network_alias(net, fam), "network", prefixes, f"Network {net.name} (IPv{fam})", "network", net.name))
    if out:
        out.append(Alias(naming.network_alias(net), "network", [a.name for a in out], f"Network {net.name}", "network", net.name))
    return out


def host_aliases(host: Host) -> list[Alias]:
    every = [ipaddress.ip_address(a) for i in host.interfaces for a in i.all_addresses]
    v4 = sorted({str(a) for a in every if a.version == 4}, key=ipaddress.ip_address)
    v6 = sorted({str(a) for a in every if a.version == 6}, key=ipaddress.ip_address)
    out = []
    label = f"{host.name}" + (f" ({host.fun})" if host.fun else "")
    if v4:
        out.append(Alias(naming.host_alias(host, "4"), "host", v4, f"Host {label} (IPv4)", "host", host.name))
    if v6:
        out.append(Alias(naming.host_alias(host, "6"), "host", v6, f"Host {label} (IPv6)", "host", host.name))
    if out:
        out.append(Alias(naming.host_alias(host), "host", [a.name for a in out], f"Host {label}", "host", host.name))
    return out


def generated() -> list[Alias]:
    nets = db.session.scalars(db.select(Network).order_by(Network.vlan.is_(None), Network.vlan, Network.name)).all()
    out: list[Alias] = []
    by_zone: dict[str, list[str]] = {}
    for net in nets:
        aliases = network_aliases(net)
        out.extend(aliases)
        if aliases:
            by_zone.setdefault(net.zone, []).append(aliases[-1].name)

    zone_aliases = []
    for zone in naming.all_zones():
        if zone in by_zone:
            zone_aliases.append(Alias(naming.zone_group(zone), "network", by_zone[zone], f"Zone {zone}: {naming.zone_long(zone)}", "zone", zone))
    out.extend(zone_aliases)

    for area in db.session.scalars(db.select(Area).order_by(Area.position)).all():
        members = [a.name for a in zone_aliases if a.source.split("_")[0] == area.code]
        if members:
            out.append(Alias(naming.area_group(area.code), "network", members, f"Area {area.code}: {area.name}", "area", area.code))

    for host in _live_hosts():
        out.extend(host_aliases(host))
    return out


def resolve_group(group: AliasGroup, generated_names: set[str]) -> Alias:
    """Turn member references into alias content.

    host:<name>     -> the host's dual-stack alias (or its MACs for mac groups)
    network:<name>  -> the network's dual-stack alias
    zone:<CODE>     -> NG_ZONE_<CODE> (an area's default zone: NG_ZONE_<AREA>_DEFAULT)
    area:<CODE>     -> NG_ZONE_<CODE>
    alias:<NAME>    -> that alias by name
    anything else   -> used literally (address, CIDR, port, MAC, URL)
    """
    content, errors = [], []
    for raw in group.members:
        ref = str(raw).strip()
        kind, _, value = ref.partition(":")
        if not value or kind not in {"host", "network", "zone", "area", "alias"}:
            content.append(ref)
            continue
        if kind == "host":
            from ..store import find_host
            host = find_host(value)
            if host is None:
                errors.append(f"unknown host '{value}'")
            elif group.type == "mac":
                macs = [i.mac for i in host.interfaces if i.mac]
                content.extend(macs) if macs else errors.append(f"host '{value}' has no MAC address")
            elif naming.host_alias(host) in generated_names:
                content.append(naming.host_alias(host))
            else:
                errors.append(f"host '{value}' has no addresses (or is offline)")
        elif kind == "network":
            net = db.session.scalar(db.select(Network).where(Network.name == value))
            if net is None:
                errors.append(f"unknown network '{value}'")
            elif naming.network_alias(net) in generated_names:
                content.append(naming.network_alias(net))
            else:
                errors.append(f"network '{value}' has no prefix")
        elif kind in {"zone", "area"}:
            name = naming.zone_group(value) if kind == "zone" else naming.area_group(value)
            content.append(name) if name in generated_names else errors.append(f"no networks in {kind} '{value}'")
        else:  # alias, by labdex name or firewall id
            content.append(value) if value in generated_names else errors.append(f"unknown alias '{value}'")
    return Alias(group.name, group.type, content, group.description, "custom", group.name, errors, group.fw_id or "")


def firewall_ids() -> dict[str, str]:
    return {a.name: a.fw_id for a in all_aliases()}


def all_aliases() -> list[Alias]:
    from ..cache import memo
    return list(memo("aliases", _all_aliases))


def _all_aliases() -> list[Alias]:
    gen = generated()
    names = {a.name for a in gen}
    groups = db.session.scalars(db.select(AliasGroup).order_by(AliasGroup.name)).all()
    names |= {g.name for g in groups} | {g.fw_id for g in groups if g.fw_id}
    out = gen + [resolve_group(g, names) for g in groups]
    # firewall ids: given (custom aliases may set one) or derived; unique
    used: dict[str, str] = {}
    for a in out:
        fid = a.fw_id or naming.firewall_id(a.name)
        if fid in used and used[fid] != a.name:
            import hashlib
            fid = f"{fid[:26].rstrip('_')}_{hashlib.sha1(a.name.encode()).hexdigest()[:5].upper()}"
        used[fid] = a.name
        a.fw_id = fid
    # members point at other aliases by labdex name: translate to their firewall ids
    by_name = {a.name: a.fw_id for a in out}
    for a in out:
        a.content = [by_name.get(c, c) for c in a.content]
    return out
