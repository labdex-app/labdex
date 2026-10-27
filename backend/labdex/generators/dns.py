"""DNS records derived from the inventory.

- one A/AAAA record per interface:   <host>.<network>.<domain>, or <host>.<zone> when the network
                                     has a DNS zone of its own ('@' = straight into the domain)
- short name CNAME:                  <host>.<host zone> -> primary interface (host zone: the domain
                                     unless the host sets its own)
- fun name CNAME:                    <fun>.<domain>   -> primary interface
- service CNAME:                     <dns_name>.<domain> -> primary interface
- service SRV (+TXT):                _<service>._<proto>.<domain> -> port + interface
- the host's own extra records (HostRecord): CNAME, A/AAAA, TXT, SRV, MX

SRV/TXT follow the DNS-SD layout, so a dashboard (or anything else) can find
services by querying DNS instead of keeping its own list.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .. import naming
from ..models import Host, db


@dataclass
class Record:
    name: str  # relative to its zone
    type: str
    value: str
    host: str = ""
    zone: str = ""  # '' = the inventory domain
    public: bool = False  # split horizon: a public service's name, answered with the internal address

    def fqdn(self, domain: str) -> str:
        return self.zone or domain if self.name == "@" else f"{self.name}.{self.zone or domain}"


def record_name(host: str, iface, per_net: dict[str, int]) -> str:
    """<host>, or <host>-<label> when the host has several interfaces in the same
    network (a notebook's wired and Wi-Fi NIC). The network part is in the parent."""
    if per_net.get(iface.network.name, 0) > 1 and iface.label:
        return f"{host}-{iface.label}"
    return host


def split_name(parent: str, domain: str) -> tuple[str, str]:
    """A parent like 'srv.lab.example.net' as (label prefix, zone): ('srv', 'lab.example.net') when it's
    inside the domain, else ('', parent): the parent is a zone of its own."""
    if parent == domain:
        return "", domain
    if parent.endswith("." + domain):
        return parent[: -len(domain) - 1], domain
    return "", parent


def _target(value: str, domain: str) -> str:
    """Targets with a dot are absolute; a single label is relative to the domain."""
    v = value.strip().lower()
    if not v:
        return v
    if v.endswith("."):
        return v
    return f"{v}." if "." in v else f"{v}.{domain}."


def valid_name(n: str) -> bool:
    """A relative DNS name: labels, `_service` labels, and a wildcard `*` as the leftmost label."""
    if naming.DNS_NAME_RE.match(n):
        return True
    parts = n.split(".")
    return all(part and (part[0] == "_" or naming.DNS_NAME_RE.match(part) or (part == "*" and i == 0)) for i, part in enumerate(parts))


def expand_record(name: str, rtype: str, value: str, *, host: str, fun: str, domain: str,
                  target: str, ip4: str, ip6: str) -> tuple[str, str]:
    """(name relative to `domain` (the host's zone), value) for a host's extra record; ValueError
    when it can't be built. An absolute name must lie inside that zone."""
    try:
        n = (name or "").strip().lower().format(host=host, fun=fun or host)
        v = (value or "").strip().format(target=target, fqdn=f"{host}.{domain}.", ip4=ip4, ip6=ip6, host=host)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"unknown placeholder {exc}") from exc
    n = n or host
    if n == "@":
        raise ValueError("the domain apex can't belong to a host")
    if n.endswith("."):
        if not (n == f"{domain}." or n.endswith(f".{domain}.")):
            raise ValueError(f"{n} is outside {domain}")
        n = n[: -len(domain) - 2]
    if not valid_name(n):
        raise ValueError(f"'{n}' isn't a valid DNS name")
    t = rtype.upper()
    if t == "CNAME":
        v = _target(v or target, domain)
    elif t in ("A", "AAAA"):
        import ipaddress
        ip = ipaddress.ip_address(v)  # ValueError if not an address
        if (t == "A") != (ip.version == 4):
            raise ValueError(f"{v} isn't an IPv{4 if t == 'A' else 6} address")
        v = str(ip)
    elif t == "MX":
        pref, exchange = v.split()
        v = f"{int(pref)} {_target(exchange, domain)}"
    elif t == "SRV":
        prio, weight, port, tgt = v.split()
        v = f"{int(prio)} {int(weight)} {int(port)} {_target(tgt, domain)}"
    elif t == "TXT":
        if not v:
            raise ValueError("TXT needs text")
        if not v.startswith('"'):
            v = '"' + v.replace('"', '\\"') + '"'
    else:
        raise ValueError(f"unsupported type {rtype}")
    return n, v


def records() -> list[Record]:
    from ..cache import memo
    return list(memo("dns.records", _records))


def _records() -> list[Record]:
    from .. import naming as nm
    domain = nm.get_setting("domain")
    out: list[Record] = []
    hosts = db.session.scalars(db.select(Host).where(Host.status != "offline").order_by(Host.name)).all()
    from ..models import Network
    own_zones = {domain} | {nm.network_dns_zone(n) for n in db.session.scalars(db.select(Network))} | {nm.host_dns_zone(h) for h in hosts}
    for host in hosts:
        per_net: dict[str, int] = {}
        for iface in host.interfaces:
            if iface.network:
                per_net[iface.network.name] = per_net.get(iface.network.name, 0) + 1
        addr_names: dict[int, tuple[str, str]] = {}  # interface id -> (name, zone)
        used: dict[tuple[str, str], str] = {}  # (name, zone) -> network that took it
        for iface in host.interfaces:
            net = iface.network
            if not net or not iface.dns_enabled:
                continue
            prefix, zone = split_name(nm.network_dns_parent(net), domain)
            label = record_name(host.name, iface, per_net)
            name = f"{label}.{prefix}" if prefix else label
            if used.get((name, zone), net.name) != net.name:
                name = f"{label}-{net.name}" + (f".{prefix}" if prefix else "")  # two networks share a parent
            used[(name, zone)] = net.name
            addr_names[iface.id] = (name, zone)
            if iface.ip4:
                out.append(Record(name, "A", iface.ip4, host.name, zone))
            for a6 in iface.ip6s:  # one AAAA per IPv6 prefix of the network (ULA, GUA, …)
                out.append(Record(name, "AAAA", a6, host.name, zone))
        p = nm.primary_interface(host)
        if p is None or p.id not in addr_names:
            continue
        pname, pzone = addr_names[p.id]
        target = f"{pname}.{pzone}."
        hz = nm.host_dns_zone(host)
        taken = {(n, z) for n, z in addr_names.values()}

        def cname(name: str, zone: str = hz, taken=taken, target=target, host=host):
            if (name, zone) not in taken:  # the address record already has this name (network zone '@')
                out.append(Record(name, "CNAME", target, host.name, zone))
        cname(host.name)
        no_dns = set(host.fun_no_dns or [])
        for fun in host.fun_names:
            if fun not in no_dns:
                cname(fun)
        for svc in host.services:
            if svc.dns_name and svc.dns_name.endswith("."):
                # an absolute name (grafana.example.net.): a record only inside a zone labdex writes
                fq = svc.dns_name.rstrip(".")
                z = next((z for z in sorted(own_zones, key=len, reverse=True) if fq.endswith("." + z)), None)
                if z:
                    cname(fq[: -len(z) - 1], z)
            elif svc.dns_name:
                cname(svc.dns_name)
            if svc.port and svc.publish_srv:
                srv = f"_{svc.name}._{svc.proto or 'tcp'}"
                out.append(Record(srv, "SRV", f"0 0 {svc.port} {target}", host.name, hz))
                txt = [f"scheme={svc.scheme}"] if svc.scheme else []
                if svc.path:
                    txt.append(f"path={svc.path}")
                if txt:
                    out.append(Record(srv, "TXT", " ".join(f'"{t}"' for t in txt), host.name, hz))
        for svc in host.services:  # split horizon: inside, the public name points straight at the host
            if svc.public and svc.public_name:
                from ..store import service_addresses
                for a in service_addresses(host, svc):
                    out.append(Record("@", "AAAA" if ":" in a else "A", a, host.name, svc.public_name, public=True))
        for rec in host.records:
            try:
                n, v = expand_record(rec.name, rec.type, rec.value, host=host.name, fun=host.fun or "", domain=hz,
                                     target=target, ip4=p.ip4 or "", ip6=(p.ip6s or [""])[0])
            except ValueError:
                continue  # save_host refuses these; a later change (address removed) is reported by lint
            out.append(Record(n, rec.type.upper(), v, host.name, hz))
    # the inventory domain is written as '' so Record.fqdn(domain) and older callers agree
    for r in out:
        if r.zone == domain:
            r.zone = ""
    return out


def zones(recs: list[Record] | None = None) -> list[str]:
    """Every DNS zone labdex writes records into, the inventory domain first."""
    from .. import naming as nm
    domain = nm.get_setting("domain")
    recs = records() if recs is None else recs
    return [domain] + sorted({r.zone for r in recs if r.zone and r.zone != domain})


def bind(recs: list[Record] | None = None) -> str:
    """One block per zone, each with its $ORIGIN."""
    recs = records() if recs is None else recs
    domain = naming.get_setting("domain")
    lines = ["; Generated by labdex. Paste each $ORIGIN block into its zone or import it into your DNS server.", "$TTL 300"]
    for zone in zones(recs):
        zrecs = [r for r in recs if (r.zone or domain) == zone]
        if not zrecs:
            continue
        width = max([len(r.name) for r in zrecs] + [8])
        lines += ["", f"$ORIGIN {zone}."]
        current = None
        for r in zrecs:
            if r.host != current:
                lines.append(f"; {r.host}")
                current = r.host
            lines.append(f"{r.name:<{width}}  IN  {r.type:<5}  {r.value}")
    return "\n".join(lines) + "\n"


def as_json(recs: list[Record] | None = None) -> str:
    recs = records() if recs is None else recs
    domain = naming.get_setting("domain")
    return json.dumps([{**asdict(r), "fqdn": r.fqdn(domain)} for r in recs], indent=2) + "\n"
