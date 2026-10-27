"""Reading and writing inventory objects from plain dicts.

The REST API and the YAML import use the same functions, so a payload that the
API accepts is exactly a valid YAML entry and vice versa. Hard validation
(wrong formats, unknown references, duplicates) happens here and rejects the
write; softer convention checks live in lint.py and only warn.
"""
from __future__ import annotations

import ipaddress
import re

from . import ipam, naming
from .models import prefix_key as _pkey
from .models import CONNECTORS, HOST_KINDS, INTERFACE_KINDS, LINK_MEDIA, VIP_PROTOCOLS, AliasGroup, Area, Host, Interface, Network, NetworkPrefix, Service, Setting, Tag, db


class ValidationError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


class _Errors:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, field, message):
        self.items.append({"field": field, "message": message})

    def raise_if_any(self):
        if self.items:
            raise ValidationError(self.items)


def _s(data: dict, key: str) -> str:
    v = data.get(key)
    return "" if v is None else str(v).strip()


def norm_mac(mac: str) -> str:
    hexonly = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(hexonly) != 12:
        return ""
    return ":".join(hexonly[i:i + 2] for i in range(0, 12, 2)).lower()


# ===================================================================== networks
def network_to_dict(n: Network, with_stats=False) -> dict:
    d = {
        "name": n.name, "vlan": n.vlan, "zone": n.zone, "prefix4": n.prefix4, "prefix6": n.prefix6,
        "gateway4": n.gateway4, "dhcp_start": n.dhcp_start, "dhcp_end": n.dhcp_end,
        "in_dns": n.in_dns, "dns_zone": n.dns_zone or "", "notes": n.notes,
        "prefixes": [prefix_to_dict(p) for p in n.prefixes],
    }
    if with_stats:  # API views (not history snapshots or exports)
        d["uid"] = n.uid
        d["zone_long"] = naming.zone_long(n.zone)
        d["stats"] = ipam.stats(n)
        d["hosts"] = len({i.host_id for i in n.interfaces})
        d["dns_suffix"] = naming.network_dns_parent(n) if n.in_dns else ""
    return d


def save_network(data: dict, existing: Network | None = None) -> Network:
    e = _Errors()
    name = _s(data, "name").lower()
    if not naming.NET_NAME_RE.match(name):
        e.add("name", "lowercase letters, digits and dashes, starting with a letter (max 32)")
    other = db.session.scalar(db.select(Network).where(Network.name == name))
    if other is not None and other is not existing:
        e.add("name", f"network '{name}' already exists")

    vlan = data.get("vlan")
    if vlan in ("", None):
        vlan = None
    else:
        try:
            vlan = int(vlan)
            if not 1 <= vlan <= 4094:
                raise ValueError
            other = db.session.scalar(db.select(Network).where(Network.vlan == vlan))
            if other is not None and other is not existing:
                e.add("vlan", f"VLAN {vlan} is already used by '{other.name}'")
        except (TypeError, ValueError):
            e.add("vlan", "must be 1–4094 or empty")

    zone = _s(data, "zone").upper()
    if naming.parse_zone(zone) is None:
        e.add("zone", f"unknown zone '{zone}'")

    prefixes = _validate_prefixes(data, e, existing)
    dns_zone = naming.clean_zone(_s(data, "dns_zone"))
    if dns_zone and dns_zone != "@" and not naming.DNS_NAME_RE.match(dns_zone):
        e.add("dns_zone", "a DNS zone like home.lab, @ for the domain itself, or empty for <network>.<domain>")
    e.raise_if_any()

    n = existing or Network()
    n.name, n.vlan, n.zone = name, vlan, zone
    # the old single-prefix fields mirror the first prefix of each family (everything else reads them)
    first4 = next((p for p in prefixes if p["family"] == 4), None)
    first6 = next((p for p in prefixes if p["family"] == 6 and p["prefix"]), None)
    n.prefix4 = first4["prefix"] if first4 else ""
    n.gateway4 = first4["gateway"] if first4 else ""
    n.dhcp_start, n.dhcp_end = (first4["dhcp_start"], first4["dhcp_end"]) if first4 else ("", "")
    n.prefix6 = first6["prefix"] if first6 else ""
    n.prefixes.clear()
    db.session.flush()
    for i, p in enumerate(prefixes):
        n.prefixes.append(NetworkPrefix(**p, position=i))
    n.in_dns = bool(data.get("in_dns", True))
    n.dns_zone = dns_zone
    n.notes = _s(data, "notes")
    if existing is None:
        db.session.add(n)
    return n


# --------------------------------------------------------------- prefixes
PREFIX_FIELDS = ("family", "prefix", "source", "delegated_from", "prefix_id", "length", "gateway", "dhcp_start", "dhcp_end", "description")


def prefix_to_dict(p: NetworkPrefix) -> dict:
    d = {k: getattr(p, k) for k in PREFIX_FIELDS}
    if p.source != "delegated":
        for k in ("delegated_from", "prefix_id", "length"):
            d.pop(k)
    return d


def _addr_in(prefix: ipaddress._BaseNetwork | None, value: str, family: int, field: str, e, *, suffix_only: bool = False) -> str:
    """A full address, or a suffix (254, .254, ::1) added to the prefix. Returns the stored form:
    full for a known prefix, the suffix itself when the prefix is delegated (unknown here)."""
    value = (value or "").strip()
    if not value:
        return ""
    if suffix_only:  # delegated: only an interface ID makes sense
        try:
            ipaddress.IPv6Address(value if value.startswith("::") else "::" + value.lstrip(":"))
            return value if value.startswith("::") else "::" + value.lstrip(":")
        except ValueError:
            e.add(field, "an interface ID like ::1 (the prefix comes from the firewall)")
            return ""
    try:
        a = ipaddress.ip_address(value)
        # '::1' parses as an address too: when it isn't inside the prefix it is meant as a suffix
        if not (family == 6 and value.startswith("::") and prefix is not None and a not in prefix):
            if a.version != family:
                e.add(field, f"an IPv{family} address")
                return ""
            if prefix is not None and a not in prefix:
                e.add(field, f"{value} is outside {prefix}")
            return str(a)
    except ValueError:
        pass
    if prefix is None:
        e.add(field, "a full address")
        return ""
    try:  # suffix
        if family == 4:
            off = int(value.lstrip("."))
        else:
            off = int(ipaddress.IPv6Address(value if value.startswith("::") else "::" + value.lstrip(":")))
        a = prefix.network_address + off
        if a not in prefix:
            raise ValueError
        return str(a)
    except (ValueError, ipaddress.AddressValueError):
        e.add(field, f"an address in {prefix}, or a suffix like {'254' if family == 4 else '::1'}")
        return ""


def _validate_prefixes(data: dict, e, existing) -> list[dict]:
    raw = data.get("prefixes")
    if raw is None:  # older clients and YAML: the single-prefix fields
        raw = []
        if _s(data, "prefix4"):
            raw.append({"family": 4, "prefix": _s(data, "prefix4"), "gateway": _s(data, "gateway4"),
                        "dhcp_start": _s(data, "dhcp_start"), "dhcp_end": _s(data, "dhcp_end")})
        if _s(data, "prefix6"):
            raw.append({"family": 6, "prefix": _s(data, "prefix6")})
        legacy = True
    else:
        legacy = False
    out, nets = [], []
    for idx, rp in enumerate(raw):
        f = "prefix4" if legacy and idx == 0 and rp.get("family") == 4 else ("prefix6" if legacy else f"prefixes[{idx}]")
        def field(k, f=f):
            if not legacy:
                return f"{f}.{k}"
            return f if k == "prefix" else ("gateway4" if k == "gateway" and f == "prefix4" else k)
        source = _s(rp, "source") or "static"
        pstr = _s(rp, "prefix")
        try:
            family = int(rp.get("family") or (6 if ":" in pstr else 4))
        except (TypeError, ValueError):
            family = 0
        if family not in (4, 6):
            e.add(field("family"), "4 or 6")
            continue
        item = {"family": family, "prefix": "", "source": source, "delegated_from": "", "prefix_id": None, "length": None,
                "gateway": "", "dhcp_start": "", "dhcp_end": "", "description": _s(rp, "description")[:64]}
        net = None
        if source == "delegated":
            if family != 6:
                e.add(field("source"), "only IPv6 prefixes are delegated")
                continue
            item["delegated_from"] = _s(rp, "delegated_from").lower() or "wan"
            if not re.match(r"^[a-z0-9_.-]{1,32}$", item["delegated_from"]):
                e.add(field("delegated_from"), "the firewall's WAN interface, e.g. wan or opt1")
            try:
                item["prefix_id"] = int(str(rp.get("prefix_id", "")).strip() or "0", 0)  # 0x64 or 100
                item["length"] = int(rp.get("length") or 64)
                if not (0 <= item["prefix_id"] < 2 ** 16 and 48 <= item["length"] <= 64):
                    raise ValueError
            except (TypeError, ValueError):
                e.add(field("prefix_id"), "a prefix ID (decimal or 0x…) and a length between /48 and /64")
            item["prefix"] = pstr  # last seen (kept by the delegation sync)
            for k in ("gateway", "dhcp_start", "dhcp_end"):
                item[k] = _addr_in(None, _s(rp, k), 6, field(k), e, suffix_only=True)
        elif source == "static":
            try:
                net = ipaddress.ip_network(pstr, strict=True)
                if net.version != family:
                    raise ValueError(f"not an IPv{family} prefix")
                item["prefix"] = str(net)
                nets.append((net, field("prefix")))
            except ValueError as exc:
                e.add(field("prefix"), f"not a valid IPv{family} network ({exc})" if pstr else "give the prefix, e.g. 10.0.10.0/24")
                if bool(_s(rp, "dhcp_start")) != bool(_s(rp, "dhcp_end")):
                    e.add(field("dhcp_end"), "set both ends of the DHCP range, or neither")
                continue
            for k in ("gateway", "dhcp_start", "dhcp_end"):
                item[k] = _addr_in(net, _s(rp, k), family, field(k), e)
        else:
            e.add(field("source"), "static or delegated")
            continue
        if bool(item["dhcp_start"]) != bool(item["dhcp_end"]):
            e.add(field("dhcp_end"), "set both ends of the DHCP range, or neither")
        out.append(item)
    # no overlaps, inside this network or with other networks
    for i, (a, fa) in enumerate(nets):
        for b, _ in nets[:i]:
            if a.overlaps(b):
                e.add(fa, f"{a} overlaps {b}")
    others = db.session.scalars(db.select(NetworkPrefix).where(NetworkPrefix.source == "static")).all()
    for a, fa in nets:
        for o in others:
            if existing is not None and o.network_id == existing.id:
                continue
            try:
                b = ipaddress.ip_network(o.prefix)
            except ValueError:
                continue
            if a.version == b.version and a.overlaps(b):
                e.add(fa, f"{a} overlaps {b} of network {o.network.name}")
    return out


# ======================================================================== hosts
def host_to_dict(h: Host, derived=False) -> dict:
    d = {
        "name": h.name, "fun": h.fun or "", "fun_names": h.fun_names, "zone": h.zone, "zone_pinned": bool(h.zone_pinned),
        "dns_zone": h.dns_zone or "",
        "kind": h.kind or "device", "status": h.status, "function": h.function,
        "hardware": h.hardware, "vendor": h.vendor or "", "model": h.model or "", "icon": h.icon or "", "colour": h.colour or "", "runs_on": h.runs_on.name if h.runs_on else "", "notes": h.notes,
        "rack": h.rack.name if h.rack else "", "rack_unit": h.rack_unit, "rack_height": h.rack_height or 1, "rack_face": h.rack_face or "front",
        "fun_no_dns": list(h.fun_no_dns or []),
        "tags": list(h.tags or []),
        "features": list(h.features or []),
        "vars": h.vars or {},
        "interfaces": [
            {"network": i.network.name if i.network else "", "zone": i.network.zone if i.network else "", "label": i.label, "ip4": i.ip4,
             "ip6": i.ip6, "ip6_token": i.ip6_token or "", "ip6_overrides": dict(i.ip6_overrides or {}), "ip6_all": i.ip6s,
             "mac": i.mac, "primary": i.primary, "in_dns": i.in_dns, "kind": i.kind or "", "parent": i.parent or "",
             "extra_ips": list(i.extra_ips or []), "connector": i.connector or "", "speed": i.speed or ""}
            for i in h.interfaces
        ],
        "cables": host_links(h),
        "rules": [dict(r) for r in h.rules or []],
        "services": [
            {"name": s.name, "port": s.port, "proto": s.proto, "scheme": s.scheme, "path": s.path,
             "dns_name": s.dns_name, "allow_from": list(s.allow_from or []), "publish_srv": bool(s.publish_srv),
             "description": s.description or "", "bind": s.bind or "", "link_to": s.link_to or "", "addresses": service_addresses(h, s),
             "public": bool(s.public), "public_port": s.public_port, "public_name": s.public_name or ""}
            for s in h.services
        ],
        "records": [{"name": r.name, "type": r.type, "value": r.value} for r in h.records],
    }
    if h.is_vip:
        d["vip_protocol"] = h.vip_protocol or ""
        d["vip_members"] = [m.name for m in h.vip_members]
    else:
        d["vips"] = [{"name": v.name, "fun": v.fun or "", "protocol": v.vip_protocol or "",
                      "addresses": [{"network": i.network.name if i.network else "", "ip4": i.ip4, "ip6": i.ip6} for i in v.interfaces]}
                     for v in h.vips]
    if derived:
        from .generators import aliases as galiases
        from .generators import dns, hostfiles
        d["zone_long"] = naming.zone_long(h.zone)
        d["zone_colour"] = naming.zone_colour(h.zone)
        d["name_ok"] = bool(naming.hostname_re().match(h.name))
        fw = galiases.firewall_ids()
        d["aliases"] = [a.as_dict() | {"fw_id": fw.get(a.name, a.name), "length": len(fw.get(a.name, a.name))}
                        for a in galiases.host_aliases(h)]
        domain = naming.get_setting("domain")
        d["dns"] = [{"fqdn": r.fqdn(domain), "type": r.type, "value": r.value}
                    for r in dns.records() if r.host == h.name]
        d["guests"] = [g.name for g in db.session.scalars(db.select(Host).where(Host.runs_on_id == h.id).order_by(Host.name))]
        d["motd"] = hostfiles.motd(h)
        d["seen"] = seen_addresses(h)
        d["links"] = [
            {"name": s.name, "url": service_url(h, s)} for s in h.services if service_url(h, s)
        ]
    return d


def _bind_matches_token(bind: str, data: dict) -> bool:
    """A bind address that is one of the IPv6 addresses an interface gets from its token."""
    from types import SimpleNamespace
    from .models import ip6_plan
    for i in data.get("interfaces") or []:
        net = db.session.scalar(db.select(Network).where(Network.name == str(i.get("network") or "")))
        try:
            plan = ip6_plan(SimpleNamespace(network=net, ip6=str(i.get("ip6") or ""), ip6_token=str(i.get("ip6_token") or ""),
                                            mac=str(i.get("mac") or ""), ip6_overrides=i.get("ip6_overrides") or {}))
        except ValueError:
            continue
        if bind in {a for a, _ in plan}:
            return True
    return False


def service_addresses(h: Host, s: Service) -> list[str]:
    """The addresses a service listens on, from its bind setting."""
    b = (s.bind or "").strip()
    if not b:
        return [a for i in h.interfaces for a in i.all_addresses]
    if b == "primary":
        p = naming.primary_interface(h)
        return p.addresses if p is not None else []
    by_net = [a for i in h.interfaces if i.network is not None and i.network.name == b for a in i.addresses]
    return by_net or [b]


def service_url(h: Host, s: Service) -> str:
    if s.scheme not in ("http", "https"):
        return ""
    if (s.link_to or "") == "ip":
        addrs = service_addresses(h, s)
        a = next((x for x in addrs if ":" not in x), addrs[0] if addrs else "")
        if not a:
            return ""
        a = f"[{a}]" if ":" in a else a
        default = {"http": 80, "https": 443}[s.scheme]
        port = f":{s.port}" if s.port and s.port != default else ""
        return f"{s.scheme}://{a}{port}{s.path or ''}"
    if s.dns_name and s.dns_name.endswith("."):
        host = s.dns_name.rstrip(".")
    elif s.dns_name:
        host = f"{s.dns_name}.{naming.host_dns_zone(h)}"
    else:
        p = naming.primary_interface(h)
        if p is None:
            return ""
        host = f"{h.name}.{naming.network_dns_parent(p.network)}"
    default = {"http": 80, "https": 443}[s.scheme]
    port = f":{s.port}" if s.port and s.port != default and not s.dns_name else ""
    return f"{s.scheme}://{host}{port}{s.path or ''}"


def find_host(name: str) -> Host | None:
    """By hostname, any of its fun names, or its stable id."""
    name = (name or "").lower()
    h = db.session.scalar(db.select(Host).where((Host.name == name) | (Host.fun == name)))
    if h is None and name:
        h = db.session.scalar(db.select(Host).where(Host.uid == name))
    if h is None and name:
        h = next((x for x in db.session.scalars(db.select(Host)) if name in (x.fun_extra or [])), None)
    return h


def find_network(name: str) -> Network | None:
    """By name or its stable id."""
    name = (name or "").lower()
    return db.session.scalar(db.select(Network).where(Network.name == name)) or \
        db.session.scalar(db.select(Network).where(Network.uid == name))


def _fun_owner(fun: str) -> Host | None:
    return find_host(fun)


def save_host(data: dict, existing: Host | None = None) -> Host:
    e = _Errors()
    name = _s(data, "name").lower()
    if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", name):
        e.add("name", "a valid DNS label: lowercase letters, digits and dashes")
    other = find_host(name)
    if other is not None and other is not existing:
        e.add("name", f"'{name}' is already used by {other.name}" if other.name != name else f"host '{name}' already exists")

    raw_funs = data.get("fun_names")
    if raw_funs is None:
        raw_funs = [_s(data, "fun")]
    elif isinstance(raw_funs, str):
        raw_funs = re.split(r"[,\s]+", raw_funs)
    funs = list(dict.fromkeys(str(f).strip().lower() for f in raw_funs if str(f).strip()))
    for f in funs:
        if not naming.FUN_RE.match(f):
            e.add("fun", f"'{f}': lowercase letters, digits and dashes, starting with a letter")
        elif f == name:
            e.add("fun", f"'{f}' must differ from the hostname")
        else:
            clash = _fun_owner(f)
            if clash is not None and clash is not existing:
                e.add("fun", f"'{f}' is already used by {clash.name}")
    fun = funs[0] if funs else None

    zone = _s(data, "zone").upper()
    pinned_raw = data.get("zone_pinned")
    kind = _s(data, "kind") or "device"
    if kind not in HOST_KINDS:
        e.add("kind", f"one of {', '.join(HOST_KINDS)}")
    vip_protocol = _s(data, "vip_protocol").lower() if kind == "vip" else ""
    if vip_protocol and vip_protocol not in VIP_PROTOCOLS:
        e.add("vip_protocol", f"one of {', '.join(VIP_PROTOCOLS)}")
    colour = _s(data, "colour").lower()
    if colour and not re.match(r"^#[0-9a-f]{6}$", colour):
        e.add("colour", "#rrggbb, or empty for the zone's colour")
    status = _s(data, "status") or "active"
    if status not in naming.STATUSES:
        e.add("status", f"one of {', '.join(naming.STATUSES)}")

    runs_on = None
    if _s(data, "runs_on"):
        runs_on = db.session.scalar(db.select(Host).where(Host.name == _s(data, "runs_on")))
        if runs_on is None:
            e.add("runs_on", f"unknown host '{_s(data, 'runs_on')}'")
        elif existing is not None and runs_on is existing:
            e.add("runs_on", "a host can't run on itself")
    from . import racks
    rack = racks.place(data, existing, e)

    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    tags = sorted({str(t).strip().lower() for t in tags if str(t).strip()})
    bad = [t for t in tags if not TAG_RE.match(t)]
    if bad:
        e.add("tags", f"'{bad[0]}': lowercase letters, digits and - _ . : (at most 32)")

    from . import features as feat
    fpaths = data.get("features") or []
    if isinstance(fpaths, str):
        fpaths = [t for t in re.split(r"[,\s]+", fpaths) if t]
    fpaths = list(dict.fromkeys(str(p).strip().lower() for p in fpaths if str(p).strip()))
    unknown = feat.check_host_features(fpaths)
    if unknown:
        e.add("features", f"unknown feature(s): {', '.join(unknown)}")
    try:
        hvars = feat.parse_vars(data.get("vars"))
    except ValidationError as exc:
        hvars = {}
        e.add("vars", exc.errors[0]["message"])
    else:  # a secret the variables point at for the first time: it must exist, and you may hand it out
        from . import ansiblevault, secretstore
        old = ansiblevault.refs(existing.vars or {}) if existing is not None else set()
        for x in secretstore.check_new(old, ansiblevault.refs(hvars), "vars", must_exist=False):
            e.add("vars", x["message"])

    ifaces = []
    for idx, raw in enumerate(data.get("interfaces") or []):
        f = f"interfaces[{idx}]"
        net = None
        if _s(raw, "network"):
            net = db.session.scalar(db.select(Network).where(Network.name == _s(raw, "network")))
            if net is None:
                e.add(f"{f}.network", f"unknown network '{_s(raw, 'network')}'")
        ip4, ip6, mac = _s(raw, "ip4"), _s(raw, "ip6"), _s(raw, "mac")
        if ip4:
            try:
                ip4 = str(ipaddress.IPv4Address(ip4))
                if net is not None and ipam.contains(net, ip4) is False:
                    e.add(f"{f}.ip4", f"{ip4} is outside {net.name} ({', '.join(str(x) for x in ipam.known_prefixes(net, 4))})")
            except ValueError:
                e.add(f"{f}.ip4", "not a valid IPv4 address")
        if ip6:
            try:
                ip6 = str(ipaddress.IPv6Address(ip6))
                if net is not None and ipam.contains(net, ip6) is False:
                    e.add(f"{f}.ip6", f"{ip6} is outside {net.name} ({', '.join(str(x) for x in ipam.known_prefixes(net, 6))})")
            except ValueError:
                e.add(f"{f}.ip6", "not a valid IPv6 address")
        if mac:
            mac = norm_mac(mac)
            if not mac:
                e.add(f"{f}.mac", "not a valid MAC address")
        if net is None and not _s(raw, "label"):
            e.add(f"{f}.label", "give a label when the interface has no network")
        in_dns = raw.get("in_dns")
        if in_dns is not None and net is not None and bool(in_dns) == net.in_dns:
            in_dns = None  # same as the network: follow it
        token = _s(raw, "ip6_token").lower()
        if token:
            from .models import token_value
            if token != "eui64":
                token = token if token.startswith("::") else "::" + token.lstrip(":")
            tv = token_value(token, mac)
            if tv is None:
                e.add(f"{f}.ip6_token", "an interface ID like ::53, or eui64 (needs a MAC)" if token == "eui64" else "an interface ID like ::53")
            elif net is not None and any(p.family == 6 and p.prefix and tv >= ipaddress.ip_network(p.prefix).num_addresses
                                         for p in net.prefixes):
                e.add(f"{f}.ip6_token", "too large for the network's IPv6 prefixes")
            elif tv == 0:
                e.add(f"{f}.ip6_token", "::0 is the prefix itself")
        overrides = {}
        for key, addr in (raw.get("ip6_overrides") or {}).items():
            addr = str(addr or "").strip()
            if not addr:
                continue
            try:
                a6 = ipaddress.IPv6Address(addr)
                pfx = next((x for x in (net.prefixes if net is not None else []) if x.family == 6 and _pkey(x) == key), None)
                if pfx is None:
                    e.add(f"{f}.ip6_overrides", f"{key}: not an IPv6 prefix of {net.name if net is not None else 'the network'}")
                elif pfx.prefix and a6 not in ipaddress.ip_network(pfx.prefix):
                    e.add(f"{f}.ip6_overrides", f"{addr} is outside {pfx.prefix}")
                overrides[key] = str(a6)
            except ValueError:
                e.add(f"{f}.ip6_overrides", f"{addr}: not an IPv6 address")
        ikind = _s(raw, "kind").lower()
        if ikind and ikind not in INTERFACE_KINDS:
            e.add(f"{f}.kind", f"one of {', '.join(INTERFACE_KINDS)}")
        extra = []
        for a in raw.get("extra_ips") or []:
            a = str(a).strip()
            if not a:
                continue
            try:
                ad = ipaddress.ip_address(a.split("/")[0])
                if net is not None and ipam.contains(net, str(ad)) is False:
                    e.add(f"{f}.extra_ips", f"{ad} is outside {net.name}")
                if str(ad) not in extra and str(ad) not in (ip4, ip6):
                    extra.append(str(ad))
            except ValueError:
                e.add(f"{f}.extra_ips", f"{a}: not an IP address")
        connector = _s(raw, "connector").lower()
        if connector and connector not in CONNECTORS:
            e.add(f"{f}.connector", f"one of {', '.join(CONNECTORS)}")
        speed = _s(raw, "speed").upper().replace("GBIT", "G").replace("MBIT", "M")
        if speed and not re.match(r"^\d{1,4}(\.\d{1,2})?[MGT]$", speed):
            e.add(f"{f}.speed", "like 1G, 2.5G, 10G or 100M")
        parent = _s(raw, "parent")
        if len(_s(raw, "label")) > 32:
            e.add(f"{f}.label", "at most 32 characters")
        ifaces.append({"network": net, "label": _s(raw, "label"), "ip4": ip4, "ip6": ip6, "kind": ikind, "parent": parent[:96],
                       "connector": connector, "speed": speed,
                       "extra_ips": extra,
                       "mac": mac, "primary": bool(raw.get("primary")),
                       "in_dns": None if in_dns is None else bool(in_dns), "ip6_token": token, "ip6_overrides": overrides})
    if sum(1 for i in ifaces if i["primary"]) > 1:
        e.add("interfaces", "only one interface can be primary")
    names = [i["label"] for i in ifaces if i["label"]]
    for idx, i in enumerate(ifaces):
        p = i["parent"]
        if not p:
            continue
        if ":" in p:
            ph, _, pport = p.partition(":")
            if find_host(ph) is None:
                e.add(f"interfaces[{idx}].parent", f"unknown host '{ph}'")
            elif not pport:
                e.add(f"interfaces[{idx}].parent", "host:interface")
        elif p not in names:
            e.add(f"interfaces[{idx}].parent", f"no interface named '{p}' on this host")
        elif p == i["label"]:
            e.add(f"interfaces[{idx}].parent", "an interface can't sit on itself")
    links = _links(data, e) if "cables" in data else None
    rules = _host_rules(data, e) if "rules" in data else None

    # Home zone: the zone of the primary interface's network (or the first interface in a
    # network), unless pinned. Without zone_pinned in the data (YAML, older API clients),
    # a zone that differs from the network's counts as pinned.
    home = next((i["network"] for i in ifaces if i["primary"] and i["network"] is not None), None) \
        or next((i["network"] for i in ifaces if i["network"] is not None), None)
    derived_zone = home.zone if home is not None else ""
    pinned = bool(pinned_raw) if pinned_raw is not None else bool(zone) and zone != derived_zone
    if not pinned:
        if derived_zone:
            zone = derived_zone
        elif zone:
            pinned = True  # no interface in a network yet: keep the zone that was given
    if not zone:
        e.add("zone", "choose a zone, or give the host an interface in a network")
    elif naming.parse_zone(zone) is None:
        e.add("zone", f"unknown zone '{zone}'")

    members = []
    if kind == "vip":
        if not any(i["ip4"] or i["ip6"] for i in ifaces):
            e.add("interfaces", "a VIP needs its address: add an interface with an IP")
        if any(i["mac"] for i in ifaces):
            e.add("interfaces", "a VIP has no MAC of its own; leave MAC empty")
        raw_members = data.get("vip_members") or []
        if isinstance(raw_members, str):
            raw_members = [m for m in re.split(r"[,\s]+", raw_members) if m]
        for m in dict.fromkeys(str(x).strip().lower() for x in raw_members if str(x).strip()):
            mh = find_host(m)
            if mh is None:
                e.add("vip_members", f"unknown host '{m}'")
            elif mh is existing or mh.name == name:
                e.add("vip_members", "a VIP can't be its own member")
            elif mh.is_vip:
                e.add("vip_members", f"'{mh.name}' is a VIP itself")
            else:
                members.append(mh)
    elif data.get("vip_members"):
        e.add("vip_members", "only a VIP has members")
    if existing is not None and existing.is_vip is False and kind == "vip" and existing.vips:
        e.add("kind", f"{existing.name} is a member of {', '.join(v.name for v in existing.vips)}; remove it there first")

    svcs, seen = [], set()
    for idx, raw in enumerate(data.get("services") or []):
        f = f"services[{idx}]"
        sname = _s(raw, "name").lower()
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", sname):
            e.add(f"{f}.name", "lowercase letters, digits and dashes")
        if sname in seen:
            e.add(f"{f}.name", f"duplicate service '{sname}'")
        seen.add(sname)
        port = raw.get("port")
        if port in ("", None):
            port = None
        else:
            try:
                port = int(port)
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                e.add(f"{f}.port", "1–65535")
        proto = (_s(raw, "proto") or "tcp").lower()
        if proto not in ("tcp", "udp"):
            e.add(f"{f}.proto", "tcp or udp")
        dns_name = _s(raw, "dns_name").lower()
        if dns_name and not naming.DNS_NAME_RE.match(dns_name.rstrip(".")):
            e.add(f"{f}.dns_name", "relative to the host's zone (grafana, primary.dns), or a full name ending in a dot (grafana.example.net.)")
        if dns_name.endswith(".") and dns_name.count(".") < 2:
            e.add(f"{f}.dns_name", "a full name has a domain: grafana.example.net.")
        allow = raw.get("allow_from") or []
        if isinstance(allow, str):
            allow = [t for t in re.split(r"[,\s]+", allow) if t]
        allow = list(dict.fromkeys(str(a).strip() for a in allow if str(a).strip()))
        bad = [a for a in allow if not re.match(r"^[A-Za-z0-9_.:/*-]{1,64}$", a)]
        if bad:
            e.add(f"{f}.allow_from", f"zones, networks, aliases, CIDRs or 'any': {', '.join(bad)}")
        bind = _s(raw, "bind")
        if bind and bind != "primary":
            nets = {str(i.get("network") or "") for i in data.get("interfaces") or []}
            addrs = {str(i.get(k) or "") for i in data.get("interfaces") or [] for k in ("ip4", "ip6")}
            try:
                bind = str(ipaddress.ip_address(bind)) if bind not in nets else bind
            except ValueError:
                pass
            if bind not in nets and bind not in addrs and not _bind_matches_token(bind, data):
                e.add(f"{f}.bind", "empty (every address), primary, one of the host's networks, or one of its addresses")
        link_to = _s(raw, "link_to").lower()
        if link_to not in ("", "ip"):
            e.add(f"{f}.link_to", "empty (by name) or ip")
        public = bool(raw.get("public"))
        public_port = raw.get("public_port")
        if public_port in ("", None):
            public_port = None
        else:
            try:
                public_port = int(public_port)
                if not 1 <= public_port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                e.add(f"{f}.public_port", "1–65535")
        public_name = _s(raw, "public_name").lower().rstrip(".")
        if public_name and (public_name.count(".") < 1 or not naming.DNS_NAME_RE.match(public_name)):
            e.add(f"{f}.public_name", "a full name, e.g. photos.example.com")
        if public and port is None:
            e.add(f"{f}.public", "a public service needs a port")
        svcs.append({"name": sname, "port": port, "proto": proto, "scheme": _s(raw, "scheme").lower(),
                     "path": _s(raw, "path"), "dns_name": dns_name, "allow_from": allow, "publish_srv": bool(raw.get("publish_srv")),
                     "description": _s(raw, "description")[:200], "bind": bind, "link_to": link_to,
                     "public": public, "public_port": public_port if public else None, "public_name": public_name if public else ""})

    host_zone = naming.clean_zone(_s(data, "dns_zone"))
    if host_zone and not naming.DNS_NAME_RE.match(host_zone):
        e.add("dns_zone", "a DNS zone like home.lab, or empty for the domain")
    no_dns = [str(f).strip().lower() for f in (data.get("fun_no_dns") or []) if str(f).strip()]
    bad = [f for f in no_dns if f not in funs]
    if bad:
        e.add("fun_no_dns", f"not a fun name of this host: {', '.join(bad)}")

    from .generators import dns as gdns
    from .models import RECORD_TYPES, HostRecord
    recs = []
    domain = host_zone or naming.get_setting("domain")
    prim_ip = next((i for i in ifaces if i["primary"] and (i["ip4"] or i["ip6"])), None) or next((i for i in ifaces if i["ip4"] or i["ip6"]), None) or {}
    for idx, raw in enumerate(data.get("records") or []):
        f = f"records[{idx}]"
        rtype = _s(raw, "type").upper()
        rname, rvalue = _s(raw, "name"), _s(raw, "value")
        if rtype not in RECORD_TYPES:
            e.add(f"{f}.type", f"one of {', '.join(RECORD_TYPES)}")
            continue
        try:
            gdns.expand_record(rname, rtype, rvalue, host=name or "host", fun=fun or "", domain=domain,
                               target=f"{name or 'host'}.{domain}.", ip4=prim_ip.get("ip4") or "", ip6=prim_ip.get("ip6") or "")
        except ValueError as exc:
            msg = str(exc) or "invalid value"
            on_name = any(w in msg for w in ("DNS name", "outside", "apex"))
            if not on_name and "placeholder" not in msg:
                if rtype in ("MX", "SRV"):
                    msg = {"MX": "'<preference> <host>', e.g. 10 mail", "SRV": "'<priority> <weight> <port> <target>', e.g. 0 5 443 web"}[rtype]
                elif rtype in ("A", "AAAA") and "IPv" not in msg:
                    msg = "an address, or {ip4} / {ip6} for the primary interface's (it needs one)"
            e.add(f"{f}.name" if on_name else f"{f}.value", msg)
            continue
        recs.append({"name": rname, "type": rtype, "value": rvalue, "position": idx})
    e.raise_if_any()

    h = existing or Host()
    h.name, h.fun, h.zone, h.status = name, fun, zone, status
    h.zone_pinned, h.kind, h.vip_protocol = pinned, kind, vip_protocol
    if existing is None:
        db.session.add(h)  # before relationships, so the members' side (vips) can follow
    h.vip_members = members
    h.fun_extra = funs[1:]
    # "role" is the pre-0.5.2 name of the field: still accepted from older YAML files and API clients
    h.function = _s(data, "function") if "function" in data else _s(data, "role")
    h.hardware, h.notes, h.tags = _s(data, "hardware"), _s(data, "notes"), tags
    if "vendor" in data:
        h.vendor, h.model = _s(data, "vendor")[:100], _s(data, "model")[:100]
    if "icon" in data:
        h.icon = _s(data, "icon")[:32]
    if "colour" in data:
        h.colour = colour
    ensure_tags(tags)
    h.runs_on = runs_on
    if rack is not None:
        h.rack, h.rack_unit, h.rack_height, h.rack_face = rack["rack"], rack["rack_unit"], rack["rack_height"], rack["rack_face"]
    h.features, h.vars = fpaths, hvars
    h.fun_no_dns = [f for f in no_dns if f in funs]
    h.dns_zone = host_zone
    h.interfaces.clear()
    h.services.clear()
    h.records.clear()
    db.session.flush()
    for i in ifaces:
        h.interfaces.append(Interface(**i))
    for s in svcs:
        h.services.append(Service(**s))
    for r in recs:
        h.records.append(HostRecord(**r))
    if existing is None:
        db.session.add(h)
    if rules is not None:
        h.rules = rules
    if links is not None:
        db.session.flush()
        save_links(h, links)
    return h


# ====================================================================== aliases
def alias_to_dict(a: AliasGroup) -> dict:
    d = _alias_to_dict(a)
    if a.fw_id:
        d["fw_id"] = a.fw_id
    return d


def _alias_to_dict(a: AliasGroup) -> dict:
    return {"name": a.name, "type": a.type, "members": list(a.members or []), "description": a.description}


def save_alias(data: dict, existing: AliasGroup | None = None) -> AliasGroup:
    e = _Errors()
    name = _s(data, "name")
    if not name or len(name) > 128 or any(ord(ch) < 32 for ch in name):
        e.add("name", "1–128 characters; it can be anything readable, the firewall gets a short id")
    fw_id = _s(data, "fw_id")
    if fw_id:
        if not naming.ALIAS_NAME_RE.match(fw_id) or len(fw_id) > naming.alias_max():
            e.add("fw_id", f"letters, digits and underscores, at most {naming.alias_max()} characters")
        clash = db.session.scalar(db.select(AliasGroup).where((AliasGroup.fw_id == fw_id) | (AliasGroup.name == fw_id)))
        if clash is not None and clash is not existing:
            e.add("fw_id", f"already used by '{clash.name}'")
    other = db.session.scalar(db.select(AliasGroup).where(AliasGroup.name == name))
    if other is not None and other is not existing:
        e.add("name", f"alias '{name}' already exists")
    atype = _s(data, "type") or "host"
    if atype not in naming.ALIAS_TYPES:
        e.add("type", f"one of {', '.join(naming.ALIAS_TYPES)}")
    members = data.get("members") or []
    if isinstance(members, str):
        members = re.split(r"[\n,]+", members)
    members = [str(m).strip() for m in members if str(m).strip()]
    e.raise_if_any()

    a = existing or AliasGroup()
    a.name, a.type, a.members, a.description = name, atype, members, _s(data, "description")
    a.fw_id = fw_id
    if existing is None:
        db.session.add(a)
    return a


# =========================================================== areas, trust, meta
def save_settings(data: dict) -> None:
    for key, value in (data or {}).items():
        row = db.session.get(Setting, key) or Setting(key=key)
        row.value = str(value)
        db.session.add(row)


def seen_addresses(h: Host) -> dict:
    """What Discovery knows about the host's addresses: {ip: {hostname, vendor, mac, source, up, last_seen}}."""
    from .models import DiscoveredHost
    addrs = {a for i in h.interfaces for a in i.all_addresses}
    if not addrs:
        return {}
    out = {}
    for dh in db.session.scalars(db.select(DiscoveredHost).where(DiscoveredHost.ip.in_(addrs))):
        out[dh.ip] = {"hostname": dh.hostname or "", "vendor": dh.vendor or "", "mac": dh.mac or "", "source": dh.mac_source or dh.source or "",
                      "up": bool(dh.up), "last_seen": dh.last_seen.isoformat() + "Z" if dh.last_seen else None}
    return out


def host_links(h: Host) -> list[dict]:
    """Cables at this host, seen from it: [{port, peer, peer_port, medium, speed, notes}]."""
    from .models import Link
    out = []
    for lk in db.session.scalars(db.select(Link).where((Link.a_host_id == h.id) | (Link.b_host_id == h.id)).order_by(Link.id)):
        mine_a = lk.a_host_id == h.id
        out.append({"port": lk.a_port if mine_a else lk.b_port, "peer": (lk.b_host if mine_a else lk.a_host).name,
                    "peer_port": lk.b_port if mine_a else lk.a_port, "medium": lk.medium or "", "speed": lk.speed or "", "notes": lk.notes or "",
                    "label": lk.label or "", "length": lk.length or "", "colour": lk.colour or ""})
    return out


def _host_rules(data: dict, e) -> list[dict]:
    from . import policy
    out = []
    for idx, raw in enumerate(data.get("rules") or []):
        f = f"rules[{idx}]"
        direction = _s(raw, "direction") or "in"
        if direction not in ("in", "out"):
            e.add(f"{f}.direction", "in (to this host) or out (from it)")
        peer = _s(raw, "peer")
        if not peer:
            e.add(f"{f}.peer", "a zone, area, network, alias, address or any")
        ports = raw.get("ports") or ["any"]
        if isinstance(ports, str):
            ports = [p for p in re.split(r"[,\s]+", ports) if p]
        errs = policy.validate_ports([str(p) for p in ports])
        for x in errs:
            e.add(f"{f}.ports", x)
        out.append({"direction": direction, "peer": peer, "ports": [str(p) for p in ports], "description": _s(raw, "description")[:200]})
    return out


SELF = object()  # the peer of a cable inside one host, which may not be saved yet


def _label_taken(label: str, host_name: str) -> bool:
    """A cable label used by a cable that isn't this host's (this host's are all replaced on save)."""
    from .models import Link
    me = find_host(host_name) if host_name else None
    q = db.select(Link).where(Link.label == label)
    if me is not None:
        q = q.where(Link.a_host_id != me.id, Link.b_host_id != me.id)
    return db.session.scalar(q) is not None


def next_cable_label() -> str:
    """The next free label in the style the lab uses (C-0042 → C-0043); C-0001 to start."""
    from .models import Link
    best = None
    for (lab,) in db.session.execute(db.select(Link.label).where(Link.label != "")):
        m = re.match(r"^(.*?)(\d+)$", lab)
        if m and (best is None or int(m.group(2)) > best[1]):
            best = (m.group(1), int(m.group(2)), len(m.group(2)))
    if best is None:
        return "C-0001"
    taken = {x for (x,) in db.session.execute(db.select(Link.label))}
    n = best[1] + 1
    while f"{best[0]}{str(n).zfill(best[2])}" in taken:
        n += 1
    return f"{best[0]}{str(n).zfill(best[2])}"


def _links(data: dict, e) -> list[dict]:
    out = []
    for idx, raw in enumerate(data.get("cables") or []):
        f = f"cables[{idx}]"
        port, peer, pport = _s(raw, "port"), _s(raw, "peer"), _s(raw, "peer_port")
        if not port or len(port) > 32:
            e.add(f"{f}.port", "this host's port, e.g. eth0 (at most 32 characters)")
        me_name = str(data.get("name", "")).strip().lower()
        own = bool(peer) and peer.lower() == me_name  # a cable between two ports of this host (a loop, a patch)
        other = SELF if own else find_host(peer) if peer else None
        if own and port == pport:
            e.add(f"{f}.peer_port", "a cable inside one host joins two different ports")
        if other is None:
            e.add(f"{f}.peer", f"unknown host '{peer}'" if peer else "the host at the other end")
        if not pport or len(pport) > 32:
            e.add(f"{f}.peer_port", "the port at the other end, e.g. ge-0/0/12")
        medium = _s(raw, "medium").lower()
        if medium == "internal":
            medium = "virtual"
        if medium and medium not in LINK_MEDIA:
            e.add(f"{f}.medium", f"one of {', '.join(LINK_MEDIA)}")
        if medium == "virtual" and other is not None and not own:
            # an internal cable: inside one machine, between a guest and the host it runs on
            # (or two guests of the same host)
            me, mine_on = str(data.get("name", "")).lower(), str(data.get("runs_on") or "").lower()
            theirs_on = other.runs_on.name if other.runs_on else ""
            if not (mine_on == other.name or theirs_on == me or (mine_on and mine_on == theirs_on)):
                e.add(f"{f}.peer", f"an internal cable joins a guest and the host it runs on (or two guests of one host); "
                                   f"{other.name} isn't that for this host")
        label, length = _s(raw, "label"), _s(raw, "length").replace(",", ".").removesuffix("m").strip()
        if len(label) > 32:
            e.add(f"{f}.label", "at most 32 characters")
        elif label and _label_taken(label, me_name):
            e.add(f"{f}.label", f"{label} is on another cable already")
        if length and not re.match(r"^\d{1,4}(\.\d{1,2})?$", length):
            e.add(f"{f}.length", "metres, like 3 or 0.5")
        out.append({"port": port, "peer": other, "peer_port": pport, "medium": medium, "speed": _s(raw, "speed")[:16],
                    "notes": _s(raw, "notes")[:200], "label": label, "length": length, "colour": _s(raw, "colour")[:16]})
    seen = set()
    labels = [lk["label"] for lk in out if lk["label"]]
    for idx, lk in enumerate(out):
        if lk["label"] and labels.count(lk["label"]) > 1:
            e.add(f"cables[{idx}].label", f"{lk['label']} twice")
    for idx, lk in enumerate(out):
        if lk["medium"] == "virtual":
            continue  # a bridge holds many internal links
        if lk["port"] in seen:
            e.add(f"cables[{idx}].port", f"{lk['port']} already has a cable")
        seen.add(lk["port"])
        if lk["peer"] is SELF:
            if lk["peer_port"] in seen:
                e.add(f"cables[{idx}].peer_port", f"{lk['peer_port']} already has a cable")
            seen.add(lk["peer_port"])
    return out


def save_links(h: Host, links: list[dict]) -> None:
    """Replace the cables at this host (both ends are the same record)."""
    from .models import Link
    for lk in db.session.scalars(db.select(Link).where((Link.a_host_id == h.id) | (Link.b_host_id == h.id))):
        db.session.delete(lk)
    db.session.flush()
    for lk in links:
        if lk["peer"] is SELF:
            lk = {**lk, "peer": h}
        # the other end's port may already have a cable to somewhere else: that one goes
        # (a physical port holds one cable; a bridge inside a hypervisor holds many internal links)
        if lk["medium"] != "virtual":
            for old in db.session.scalars(db.select(Link).where(
                    (((Link.a_host_id == lk["peer"].id) & (Link.a_port == lk["peer_port"])) |
                     ((Link.b_host_id == lk["peer"].id) & (Link.b_port == lk["peer_port"]))) & (Link.medium != "virtual"))):
                db.session.delete(old)
        db.session.add(Link(a_host_id=h.id, a_port=lk["port"], b_host_id=lk["peer"].id, b_port=lk["peer_port"],
                            medium=lk["medium"], speed=lk["speed"], notes=lk["notes"], label=lk.get("label", ""),
                            length=lk.get("length", ""), colour=lk.get("colour", "")))


TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,31}$")


def ensure_tags(names) -> None:
    """Tags are known everywhere: one a host uses for the first time is made."""
    from .models import Tag
    for n in names:
        if db.session.get(Tag, n) is None:
            db.session.add(Tag(name=n))


def zone_meta(code: str) -> dict:
    from .models import Zone
    z = naming.parse_zone(code)
    row = db.session.get(Zone, code)
    return {"code": code, "uid": row.uid if row else "", "area": z.area, "long": naming.zone_long(code), "rank": z.rank, "default": z.default,
            "colour": naming.zone_colour(code), "hex": naming.zone_hex(code)}


def meta() -> dict:
    return {
        "settings": naming.settings_dict(),
        "areas": [{"code": a.code, "name": a.name, **({"description": a.description} if a.description else {}),
                   **({"colour": a.colour} if a.colour else {})} for a in db.session.scalars(db.select(Area).order_by(Area.position))],
        "zones": [zone_meta(z) for z in naming.all_zones()],
        "tags": [{"name": t.name, "colour": t.colour} for t in db.session.scalars(db.select(Tag).order_by(Tag.name)) if t.colour],
        "categories": naming.categories(),
        "statuses": naming.STATUSES,
        "alias_types": naming.ALIAS_TYPES,
        "vip_protocols": list(VIP_PROTOCOLS),
    }


def host_index() -> list[dict]:
    """Every host in name order with what list views need, built once per inventory revision.

    Entries are shared between requests: never mutate them, copy ("dict(e['host'])").
    {"host": host_to_dict, "facts": rbac facts, "net": primary network name or None,
     "zone_long", "name_ok", "links"}
    """
    from . import cache, rbac

    def build():
        out = []
        name_re = naming.hostname_re()
        for h in db.session.scalars(db.select(Host).order_by(Host.name)):
            d = host_to_dict(h) | {"uid": h.uid}
            prim = naming.primary_interface(h) or next((i for i in h.interfaces if i.network), None)
            out.append({
                "host": d, "facts": rbac.host_facts(d), "net": prim.network.name if prim is not None and prim.network is not None else None,
                "zone_long": naming.zone_long(h.zone), "name_ok": bool(name_re.match(h.name)),
                "links": [{"name": s.name, "url": u} for s in h.services if (u := service_url(h, s))],
            })
        return out
    return cache.memo("host_index", build)


def follow_network_zone(net: Network) -> list[Host]:
    """After a network moved zone: hosts that follow their primary network move with it. Returns them."""
    moved = []
    for h in db.session.scalars(db.select(Host).where(Host.zone_pinned.is_(False))):
        home = naming.home_network(h)
        if home is net and h.zone != net.zone:
            h.zone = net.zone
            moved.append(h)
    return moved
