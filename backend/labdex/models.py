"""Database models.

The inventory is deliberately small: areas and the zones in them,
networks hold address space, hosts hold interfaces and services, and alias
groups are the hand-made firewall groups. Everything else (DNS records, the
generated firewall aliases, Ansible inventory, DHCP reservations) is derived.
"""
from __future__ import annotations

import json
from datetime import datetime, UTC

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

db = SQLAlchemy()


UID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/o, 1/l/i: easy to read out and type


def new_uid() -> str:
    """A short, stable id for URLs: the name of a host, network or zone may change, this doesn't."""
    import secrets
    return "".join(secrets.choice(UID_ALPHABET) for _ in range(10))


class JSONList(TypeDecorator):
    """A list stored as JSON text (portable across SQLite and others)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(list(value or []))

    def process_result_value(self, value, dialect):
        return json.loads(value) if value else []


class JSONValue(TypeDecorator):
    """Any JSON value (dict, list, scalar) stored as text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value, sort_keys=True)

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(value)


class Setting(db.Model):
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Layout(db.Model):
    """Where the nodes of a picture sit (the topology), shared by everyone: {node id: {x, y}}."""
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    positions: Mapped[dict] = mapped_column(JSONValue, default=dict)
    updated_by: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime | None] = mapped_column(default=lambda: utcnow())


class MapView(db.Model):
    """A saved way of looking at the topology: which picture, zone, networks, options and where the
    camera was. Shared: anyone who sees the topology sees the views and can open them by link."""
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=lambda: new_uid())
    name: Mapped[str] = mapped_column(String(64), unique=True)
    config: Mapped[dict] = mapped_column(JSONValue, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime | None] = mapped_column(default=lambda: utcnow())


class Rack(db.Model):
    """A rack: hosts sit in it at a unit (the lowest one they take, counted from the bottom),
    on the front, the rear, or through the full depth."""

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=new_uid)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    site: Mapped[str] = mapped_column(String(64), default="")  # a room, a building: groups racks
    units: Mapped[int] = mapped_column(default=42)
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(default=0)


RACK_FACES = ("front", "rear", "full")


class RackItem(db.Model):
    """Something in a rack that isn't a host: a patch panel, cable management, a blank panel."""

    id: Mapped[int] = mapped_column(primary_key=True)
    rack_id: Mapped[int] = mapped_column(ForeignKey("rack.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="blank")
    label: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column(default=1)
    face: Mapped[str] = mapped_column(String(8), default="front")


class Dashboard(db.Model):
    """A page of cards on a grid: the start page, a page of links, a wall for the rack room.
    owner '' = shared with everyone (administrators change it); else the username it belongs to.
    items: [{id, type, x, y, w, h, config}] on a 12-column grid."""

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=new_uid)
    name: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(128), default="", index=True)
    position: Mapped[int] = mapped_column(default=0)
    items: Mapped[list] = mapped_column(JSONList, default=list)
    updated_by: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime | None] = mapped_column(default=lambda: utcnow())


class Area(db.Model):
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    colour: Mapped[str] = mapped_column(String(7), default="")  # #rrggbb; '' = neutral


class Zone(db.Model):
    """A zone inside an area. The area's default zone has the area's code (`DC`); others are
    `<AREA>_<SUFFIX>` (`DC_TC`), made one by one."""

    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=new_uid)
    area: Mapped[str] = mapped_column(String(16), ForeignKey("area.code"))
    name: Mapped[str] = mapped_column(String(64), default="")
    rank: Mapped[int | None] = mapped_column(nullable=True)  # higher = more trusted; None = no trust level
    colour: Mapped[str] = mapped_column(String(7), default="")  # #rrggbb; '' = the area's
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(default=0)


class Tag(db.Model):
    """A host tag known everywhere (hosts pick from these; a new one is made when first used)."""

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    colour: Mapped[str] = mapped_column(String(7), default="")
    description: Mapped[str] = mapped_column(Text, default="")


class Network(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=new_uid)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    vlan: Mapped[int | None] = mapped_column(unique=True)
    zone: Mapped[str] = mapped_column(String(48))
    prefix4: Mapped[str] = mapped_column(String(43), default="")
    prefix6: Mapped[str] = mapped_column(String(43), default="")
    gateway4: Mapped[str] = mapped_column(String(15), default="")
    dhcp_start: Mapped[str] = mapped_column(String(15), default="")
    dhcp_end: Mapped[str] = mapped_column(String(15), default="")
    in_dns: Mapped[bool] = mapped_column(default=True)
    # '' = <host>.<network>.<domain>; '@' = <host>.<domain>; anything else = <host>.<dns_zone>
    dns_zone: Mapped[str] = mapped_column(String(253), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    interfaces: Mapped[list[Interface]] = relationship(back_populates="network", lazy="selectin")
    # every address range of the network; prefix4/prefix6/gateway4/dhcp_* above mirror the first of each family
    prefixes: Mapped[list[NetworkPrefix]] = relationship(
        back_populates="network", lazy="selectin", cascade="all, delete-orphan", order_by="NetworkPrefix.position")



class NetworkPrefix(db.Model):
    """One address range of a network (IPv4 or IPv6).

    source 'static': prefix is the CIDR. source 'delegated': an IPv6 prefix the firewall derives from the
    one its WAN receives (delegated_from = WAN interface, prefix_id = the SLA/prefix ID, length = /64);
    prefix holds the last one seen ('' until known). gateway, dhcp_start/dhcp_end: full addresses for
    static prefixes, interface-ID suffixes (::1) for delegated ones.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("network.id", ondelete="CASCADE"))
    family: Mapped[int] = mapped_column(default=4)
    prefix: Mapped[str] = mapped_column(String(49), default="")
    source: Mapped[str] = mapped_column(String(16), default="static")
    delegated_from: Mapped[str] = mapped_column(String(32), default="")
    prefix_id: Mapped[int | None] = mapped_column(default=None)
    length: Mapped[int | None] = mapped_column(default=None)
    gateway: Mapped[str] = mapped_column(String(45), default="")
    dhcp_start: Mapped[str] = mapped_column(String(45), default="")
    dhcp_end: Mapped[str] = mapped_column(String(45), default="")
    description: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[int] = mapped_column(default=0)

    network: Mapped[Network] = relationship(back_populates="prefixes")


# A VIP host (kind "vip") and the hosts that hold its address.
vip_member_table = db.Table(
    "vip_member",
    db.Column("vip_id", ForeignKey("host.id", ondelete="CASCADE"), primary_key=True),
    db.Column("host_id", ForeignKey("host.id", ondelete="CASCADE"), primary_key=True),
)

HOST_KINDS = ("device", "vip")
VIP_PROTOCOLS = ("carp", "vrrp", "keepalived", "kube-vip", "metallb", "anycast", "other")


class Host(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=new_uid)
    name: Mapped[str] = mapped_column(String(63), unique=True)
    fun: Mapped[str | None] = mapped_column(String(63), unique=True)  # primary fun name (prompt, banner)
    fun_extra: Mapped[list] = mapped_column(JSONList, default=list)  # more fun names, each a CNAME
    fun_no_dns: Mapped[list] = mapped_column(JSONList, default=list)  # fun names that get no DNS record

    @property
    def fun_names(self) -> list[str]:
        return ([self.fun] if self.fun else []) + list(self.fun_extra or [])
    zone: Mapped[str] = mapped_column(String(48))  # home zone: name, host alias, permissions
    zone_pinned: Mapped[bool] = mapped_column(default=False)  # False = follow the primary interface's network
    kind: Mapped[str] = mapped_column(String(16), default="device")  # device | vip
    dns_zone: Mapped[str] = mapped_column(String(253), default="")  # '' = the domain: short name, fun names, aliases
    vip_protocol: Mapped[str] = mapped_column(String(16), default="")  # carp, vrrp, keepalived, kube-vip, …
    status: Mapped[str] = mapped_column(String(16), default="active")
    function: Mapped[str] = mapped_column(String(200), default="")  # what it does, free text ("k3s node")
    hardware: Mapped[str] = mapped_column(String(200), default="")
    vendor: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    icon: Mapped[str] = mapped_column(String(32), default="")  # '' = from the name's type part
    colour: Mapped[str] = mapped_column(String(7), default="")  # '#rrggbb' on the map; '' = the zone's
    # the host's own firewall rules: [{direction: in|out, peer: zone/area/network/alias/CIDR/any, ports: [...], description}]
    rules: Mapped[list] = mapped_column(JSONList, default=list)
    runs_on_id: Mapped[int | None] = mapped_column(ForeignKey("host.id", ondelete="SET NULL"))
    rack_id: Mapped[int | None] = mapped_column(ForeignKey("rack.id", ondelete="SET NULL"))
    rack_unit: Mapped[int | None] = mapped_column(nullable=True)  # the lowest unit it takes (1 = the bottom)
    rack_height: Mapped[int] = mapped_column(default=1)  # in units
    rack_face: Mapped[str] = mapped_column(String(8), default="front")  # front | rear | full (depth)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONList, default=list)
    features: Mapped[list] = mapped_column(JSONList, default=list)  # ["kube.cluster.oak", …]
    vars: Mapped[dict | None] = mapped_column(JSONValue, default=dict)  # extra Ansible host vars

    runs_on: Mapped[Host | None] = relationship(remote_side="Host.id", lazy="selectin")
    rack: Mapped[Rack | None] = relationship(lazy="selectin")
    interfaces: Mapped[list[Interface]] = relationship(
        back_populates="host", lazy="selectin", cascade="all, delete-orphan", order_by="Interface.id"
    )
    services: Mapped[list[Service]] = relationship(
        back_populates="host", lazy="selectin", cascade="all, delete-orphan", order_by="Service.id"
    )
    records: Mapped[list[HostRecord]] = relationship(
        back_populates="host", lazy="selectin", cascade="all, delete-orphan", order_by="HostRecord.position")
    # VIP: the hosts that hold its address. Device: the VIPs it's a member of (vips).
    vip_members: Mapped[list[Host]] = relationship(
        secondary=lambda: vip_member_table, primaryjoin=lambda: Host.id == vip_member_table.c.vip_id,
        secondaryjoin=lambda: Host.id == vip_member_table.c.host_id,
        lazy="selectin", order_by="Host.name", back_populates="vips")
    vips: Mapped[list[Host]] = relationship(
        secondary=lambda: vip_member_table, primaryjoin=lambda: Host.id == vip_member_table.c.host_id,
        secondaryjoin=lambda: Host.id == vip_member_table.c.vip_id,
        lazy="selectin", order_by="Host.name", back_populates="vip_members")

    @property
    def is_vip(self) -> bool:
        return self.kind == "vip"


class Interface(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host.id", ondelete="CASCADE"))
    network_id: Mapped[int | None] = mapped_column(ForeignKey("network.id", ondelete="SET NULL"))
    label: Mapped[str] = mapped_column(String(32), default="")
    ip4: Mapped[str] = mapped_column(String(15), default="")
    ip6: Mapped[str] = mapped_column(String(45), default="")
    mac: Mapped[str] = mapped_column(String(17), default="")
    primary: Mapped[bool] = mapped_column(default=False)
    in_dns: Mapped[bool | None] = mapped_column(default=None)  # None = follow the network
    # IPv6: an interface ID ("::53", or "eui64" from the MAC) applied to every IPv6 prefix of the network;
    # ip6 above is an explicit address (it stands for the prefix it falls in); ip6_overrides: {prefix key: address}
    ip6_token: Mapped[str] = mapped_column(String(45), default="")
    ip6_overrides: Mapped[dict | None] = mapped_column(JSONValue, default=dict)
    kind: Mapped[str] = mapped_column(String(16), default="")  # ethernet, wifi, bond, bridge, vlan, virtual, wireguard, loopback, other
    # logical stack: the interface this one sits on (a VLAN on a bond, a bond member, a VM NIC on its
    # hypervisor's bridge): "eth0" on the same host, or "host:vmbr0" on another
    parent: Mapped[str] = mapped_column(String(96), default="")
    extra_ips: Mapped[list] = mapped_column(JSONList, default=list)  # more addresses (secondary IPs), not in DNS
    connector: Mapped[str] = mapped_column(String(16), default="")  # rj45, sfp, sfp+ … (physical ports)
    speed: Mapped[str] = mapped_column(String(16), default="")  # nominal: 1G, 10G …
    # a port that passes straight through to another port of the same host (a patch panel's
    # front port and its rear port): a cable trace continues there. Set on both ports.
    passthrough: Mapped[str] = mapped_column(String(32), default="")

    @property
    def ip6s(self) -> list[str]:
        """Every IPv6 address of this interface: per IPv6 prefix of its network the override, the
        explicit address if it lies there, or prefix + token; plus an explicit address outside them."""
        return [a for a, _ in ip6_plan(self)]

    @property
    def addresses(self) -> list[str]:
        """The interface's main addresses: what DNS publishes."""
        return ([self.ip4] if self.ip4 else []) + self.ip6s

    @property
    def all_addresses(self) -> list[str]:
        """Main and secondary addresses: what the firewall and IPAM count."""
        return self.addresses + [a for a in self.extra_ips or [] if a not in self.addresses]

    @property
    def dns_enabled(self) -> bool:
        if self.network is None:
            return False
        return self.network.in_dns if self.in_dns is None else bool(self.in_dns)

    host: Mapped[Host] = relationship(back_populates="interfaces", lazy="joined")
    network: Mapped[Network | None] = relationship(back_populates="interfaces", lazy="joined")


class Link(db.Model):
    """A cable (or radio link) between two ports. A port is an interface name on a host; it need
    not be an interface labdex knows (switch ports usually aren't)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    a_host_id: Mapped[int] = mapped_column(ForeignKey("host.id", ondelete="CASCADE"))
    a_port: Mapped[str] = mapped_column(String(32))
    b_host_id: Mapped[int] = mapped_column(ForeignKey("host.id", ondelete="CASCADE"))
    b_port: Mapped[str] = mapped_column(String(32))
    medium: Mapped[str] = mapped_column(String(16), default="")  # copper, fiber, dac, wifi, virtual
    speed: Mapped[str] = mapped_column(String(16), default="")  # 1G, 10G, …
    notes: Mapped[str] = mapped_column(String(200), default="")
    label: Mapped[str] = mapped_column(String(32), default="")  # what's printed on it: C-0042
    length: Mapped[str] = mapped_column(String(12), default="")  # metres: "3", "0.5"
    colour: Mapped[str] = mapped_column(String(16), default="")  # of the jacket: blue, #ffcc00

    a_host: Mapped[Host] = relationship(foreign_keys=[a_host_id], lazy="joined")
    b_host: Mapped[Host] = relationship(foreign_keys=[b_host_id], lazy="joined")


class MacSeen(db.Model):
    """A MAC address a switch has on one of its ports (its forwarding table, from SNMP or a
    controller). Each report from a source replaces what it said before."""
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    mac: Mapped[str] = mapped_column(String(17), index=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("host.id", ondelete="SET NULL"))  # the switch
    host_name: Mapped[str] = mapped_column(String(255), default="")  # as the source called it
    port: Mapped[str] = mapped_column(String(64), default="")
    vlan: Mapped[int | None] = mapped_column()
    ip: Mapped[str] = mapped_column(String(45), default="")  # when the source knows it (a controller's client list)
    name: Mapped[str] = mapped_column(String(255), default="")  # the client's name, when the source knows it
    seen_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    host: Mapped[Host | None] = relationship(lazy="joined")


class LinkSeen(db.Model):
    """A link a source saw (LLDP/CDP on a switch, UniFi's port tables): one end is the device that
    reported it, the other its neighbour. Each report replaces what that source said before.
    The ends are matched to inventory hosts when they're recorded; unmatched ones keep what's known."""
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    protocol: Mapped[str] = mapped_column(String(16), default="")  # lldp, cdp, unifi
    a_host_id: Mapped[int | None] = mapped_column(ForeignKey("host.id", ondelete="SET NULL"))
    a_name: Mapped[str] = mapped_column(String(255), default="")  # as the source called it
    a_port: Mapped[str] = mapped_column(String(64), default="")
    b_host_id: Mapped[int | None] = mapped_column(ForeignKey("host.id", ondelete="SET NULL"))
    b_name: Mapped[str] = mapped_column(String(255), default="")
    b_port: Mapped[str] = mapped_column(String(64), default="")
    b_mac: Mapped[str] = mapped_column(String(17), default="")
    b_ip: Mapped[str] = mapped_column(String(45), default="")
    b_platform: Mapped[str] = mapped_column(String(120), default="")
    seen_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    a_host: Mapped[Host | None] = relationship(foreign_keys=[a_host_id], lazy="joined")
    b_host: Mapped[Host | None] = relationship(foreign_keys=[b_host_id], lazy="joined")


class CableIgnore(db.Model):
    """A suggested cable someone said no to (host:port|host:port, ends in sorted order)."""
    key: Mapped[str] = mapped_column(String(400), primary_key=True)
    by: Mapped[str] = mapped_column(String(128), default="")
    at: Mapped[datetime] = mapped_column(default=lambda: utcnow())


INTERFACE_KINDS = ("ethernet", "wifi", "bond", "bridge", "vlan", "virtual", "wireguard", "loopback", "other")
PHYSICAL_KINDS = ("ethernet", "wifi")  # the rest sit on something (a parent) or on nothing at all
CONNECTORS = ("rj45", "sfp", "sfp+", "sfp28", "qsfp+", "qsfp28", "qsfp-dd", "usb", "console", "radio", "other")
LINK_MEDIA = ("copper", "fiber", "dac", "wifi", "virtual")
RECORD_TYPES = ("CNAME", "A", "AAAA", "TXT", "SRV", "MX")


class HostRecord(db.Model):
    """An extra DNS record of a host, beside the generated ones.

    name: relative to the domain; '' = the host's own name; may use {host} and {fun}.
    value: may use {target} (the host's primary record), {fqdn} (host.domain),
    {ip4} and {ip6} (primary interface). Targets with a dot are absolute,
    single labels are relative to the domain. Empty CNAME value = {target}.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(253), default="")
    type: Mapped[str] = mapped_column(String(8))
    value: Mapped[str] = mapped_column(String(1024), default="")
    position: Mapped[int] = mapped_column(default=0)

    host: Mapped[Host] = relationship(back_populates="records")


class FunPool(db.Model):
    """A list of fun names to suggest from. applies_to: selectors (category:srv, zone:DC_*, *);
    empty = offered everywhere but never preferred."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    names: Mapped[list] = mapped_column(JSONList, default=list)
    applies_to: Mapped[list] = mapped_column(JSONList, default=list)
    builtin: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)


def prefix_key(p) -> str:
    """Stable name of a prefix for overrides: the CIDR, or pd:<wan>:<id> for a delegated one."""
    return f"pd:{p.delegated_from}:{p.prefix_id}" if p.source == "delegated" else p.prefix


def eui64(mac: str) -> int | None:
    """The modified EUI-64 interface ID of a MAC (RFC 4291), as an integer."""
    import re as _re
    h = _re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(h) != 12:
        return None
    b = bytearray.fromhex(h)
    b[0] ^= 0x02
    return int.from_bytes(bytes(b[:3]) + b"\xff\xfe" + bytes(b[3:]), "big")


def token_value(token: str, mac: str = "") -> int | None:
    import ipaddress as _ip
    t = (token or "").strip().lower()
    if not t:
        return None
    if t == "eui64":
        return eui64(mac)
    try:
        return int(_ip.IPv6Address(t if t.startswith("::") else "::" + t.lstrip(":")))
    except ValueError:
        return None


def ip6_plan(iface) -> list[tuple[str, str]]:
    """[(address, prefix key or '')] for an interface (see Interface.ip6s)."""
    import ipaddress as _ip
    out: list[tuple[str, str]] = []
    explicit = _ip.ip_address(iface.ip6) if iface.ip6 else None
    overrides = iface.ip6_overrides or {}
    tok = token_value(iface.ip6_token, iface.mac)
    placed = False
    for p in (iface.network.prefixes if iface.network is not None else []):
        if p.family != 6:
            continue
        key = prefix_key(p)
        if key in overrides and overrides[key]:
            out.append((str(_ip.ip_address(overrides[key])), key))
            continue
        try:
            net = _ip.ip_network(p.prefix, strict=False) if p.prefix else None
        except ValueError:
            net = None
        if net is None:
            continue
        if explicit is not None and explicit in net:
            out.append((str(explicit), key))
            placed = True
        elif tok is not None and tok < net.num_addresses:
            out.append((str(net.network_address + tok), key))
    if explicit is not None and not placed and str(explicit) not in {a for a, _ in out}:
        out.insert(0, (str(explicit), ""))
    return out


class Service(db.Model):
    __table_args__ = (UniqueConstraint("host_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(63))
    port: Mapped[int | None] = mapped_column()
    proto: Mapped[str] = mapped_column(String(8), default="tcp")
    scheme: Mapped[str] = mapped_column(String(16), default="")  # http, https, ssh, … ('' = no link)
    path: Mapped[str] = mapped_column(String(200), default="")
    dns_name: Mapped[str] = mapped_column(String(253), default="")  # service CNAME, e.g. grafana, or a full name ending in a dot
    allow_from: Mapped[list] = mapped_column(JSONList, default=list)  # zones, networks, aliases, CIDRs, "any"
    publish_srv: Mapped[bool] = mapped_column(default=False)  # DNS-SD: _name._proto SRV (+TXT)
    description: Mapped[str] = mapped_column(String(200), default="")
    # where it listens: '' = every address of the host, 'primary' = the primary interface,
    # a network name = the host's address(es) there, or one of the host's addresses
    bind: Mapped[str] = mapped_column(String(64), default="")
    link_to: Mapped[str] = mapped_column(String(8), default="")  # '' = by name (DNS), 'ip' = by address
    # reachable from the internet: a port forward on the firewall, and inside the public name resolves to the host
    public: Mapped[bool] = mapped_column(default=False)
    public_port: Mapped[int | None] = mapped_column()  # on the WAN side; empty = the same port
    public_name: Mapped[str] = mapped_column(String(253), default="")  # e.g. photos.example.com

    host: Mapped[Host] = relationship(back_populates="services")


class AliasGroup(db.Model):
    """A hand-made firewall alias (member groups and SPECIAL groups)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)  # anything readable
    fw_id: Mapped[str] = mapped_column(String(32), default="")  # name on the firewall; '' = derived from name
    type: Mapped[str] = mapped_column(String(16), default="host")
    members: Mapped[list] = mapped_column(JSONList, default=list)
    description: Mapped[str] = mapped_column(Text, default="")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ======================================================================= users
user_groups = db.Table(
    "user_groups",
    db.Column("user_id", ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    db.Column("group_id", ForeignKey("group.id", ondelete="CASCADE"), primary_key=True),
)


class User(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256), default="")  # empty = SSO only
    oidc_subject: Mapped[str | None] = mapped_column(String(320), unique=True)  # "<issuer>|<sub>" once signed in with OIDC
    is_admin: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column()

    groups: Mapped[list[Group]] = relationship(secondary=user_groups, back_populates="members")
    tokens: Mapped[list[ApiToken]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Group(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")

    members: Mapped[list[User]] = relationship(secondary=user_groups, back_populates="groups")


class Role(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    permissions: Mapped[list] = mapped_column(JSONList, default=list)  # ["host:view", "*:edit", …]
    builtin: Mapped[bool] = mapped_column(default=False)


class RoleBinding(db.Model):
    """Gives a user or group a role, limited to scopes (["*"] = everything)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("group.id", ondelete="CASCADE"))
    scopes: Mapped[list] = mapped_column(JSONList, default=list)  # "zone:DC_*", "tag:admin", "network:main", "name:srv-*"

    role: Mapped[Role] = relationship()
    user: Mapped[User | None] = relationship()
    group: Mapped[Group | None] = relationship()


class ObjectGrant(db.Model):
    """Per-object permission on top of the scoped roles."""

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("group.id", ondelete="CASCADE"))
    object_type: Mapped[str] = mapped_column(String(32))
    object_name: Mapped[str] = mapped_column(String(128))  # glob allowed
    actions: Mapped[list] = mapped_column(JSONList, default=list)

    user: Mapped[User | None] = relationship()
    group: Mapped[Group | None] = relationship()


class ApiToken(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    read_only: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column()

    user: Mapped[User] = relationship(back_populates="tokens")


# ===================================================================== history
class AuditEvent(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    actor_kind: Mapped[str] = mapped_column(String(16), default="system")  # user, token, sso, system, plugin
    action: Mapped[str] = mapped_column(String(32))  # create, update, delete, import, revert, apply, login, …
    object_type: Mapped[str] = mapped_column(String(32), index=True)
    object_name: Mapped[str] = mapped_column(String(128), default="", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict | None] = mapped_column(JSONValue)
    after: Mapped[dict | None] = mapped_column(JSONValue)
    ip: Mapped[str] = mapped_column(String(64), default="")


# ===================================================================== plugins
class Integration(db.Model):
    """An enabled instance of a plugin, with its own settings."""

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    settings: Mapped[dict] = mapped_column(JSONValue, default=dict)  # secrets stored encrypted
    state: Mapped[dict] = mapped_column(JSONValue, default=dict)  # last schedule runs etc.
    selectors: Mapped[list] = mapped_column(JSONList, default=lambda: ["*"])  # hosts it applies to (RBAC scope syntax)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class HostModule(db.Model):
    """Per-host override of an integration: on/off and host settings.

    Keyed by host name (not a foreign key) so it survives a YAML re-import;
    renames and deletes are followed in the API.
    """

    __table_args__ = (UniqueConstraint("integration_id", "host_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integration.id", ondelete="CASCADE"))
    host_name: Mapped[str] = mapped_column(String(63), index=True)
    enabled: Mapped[bool | None] = mapped_column()  # None = follow the selectors
    settings: Mapped[dict] = mapped_column(JSONValue, default=dict)

    integration: Mapped[Integration] = relationship()


class VaultKey(db.Model):
    """The KV vault's master key, wrapped with a key derived from the passphrase (scrypt). One row.
    Without the passphrase nothing in the vault can be read: not from a dump, not from a backup."""

    id: Mapped[int] = mapped_column(primary_key=True)
    salt: Mapped[str] = mapped_column(String(64))
    kdf: Mapped[str] = mapped_column(String(64))  # "scrypt:n:r:p"
    wrapped: Mapped[str] = mapped_column(Text)  # base64(nonce + AES-GCM(master))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class VaultEntry(db.Model):
    """A secret of the lab: a path, key/value fields (encrypted), optionally a host it belongs to.
    Field names, path and host are plain (they're what lists and permissions need), values aren't."""

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(12), unique=True, default=new_uid)
    path: Mapped[str] = mapped_column(String(200), unique=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("host.id", ondelete="SET NULL"), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[list] = mapped_column(JSONList, default=list)  # field names, in order
    version: Mapped[int] = mapped_column(default=1)
    updated_by: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    host: Mapped[Host | None] = relationship(lazy="selectin")


class VaultVersion(db.Model):
    """One version of an entry's values: a data key wrapped by the master key, the values under it."""

    __table_args__ = (UniqueConstraint("entry_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("vault_entry.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column()
    wrapped_key: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[str] = mapped_column(Text)
    fields: Mapped[list] = mapped_column(JSONList, default=list)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Secret(db.Model):
    """A named, write-only secret that integrations reference instead of holding a copy."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[dict] = mapped_column(JSONValue)  # {"$enc": …}
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(128), default="")


class TrustedKey(db.Model):
    """An author's plugin signing key an administrator chose to trust (community)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    key_id: Mapped[str] = mapped_column(String(16), unique=True)
    public_key: Mapped[str] = mapped_column(Text)  # base64 of the raw 32 bytes
    added_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class NamingPart(db.Model):
    """Building blocks of <category>-<type>-<role>-<nn> hostnames."""

    __table_args__ = (UniqueConstraint("kind", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # category | type | role
    code: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(default=0)


class Feature(db.Model):
    """A node in the Ansible feature tree: kube → kube.cluster → kube.cluster.oak."""

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    vars: Mapped[dict] = mapped_column(JSONValue, default=dict)
    playbooks: Mapped[list] = mapped_column(JSONList, default=list)  # relative to the playbook source
    roles: Mapped[list] = mapped_column(JSONList, default=list)


class Job(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integration.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))  # run, plan
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued → running → succeeded | failed | planned → applying → applied | failed; planned → discarded | stale
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual, hook, schedule
    params: Mapped[dict] = mapped_column(JSONValue, default=dict)
    plan: Mapped[list | None] = mapped_column(JSONValue)
    result: Mapped[dict | None] = mapped_column(JSONValue)
    log: Mapped[str] = mapped_column(Text, default="")
    inventory_rev: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    applied_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    # the queue: a job with status queued/applying and no worker is waiting to be picked up
    phase: Mapped[str] = mapped_column(String(8), default="")  # plan, run, apply (what the worker does next)
    worker: Mapped[str | None] = mapped_column(String(128), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column()

    # approvals (0.6.1): who approved, under which policy, and when it may apply
    approvals: Mapped[list] = mapped_column(JSONList, default=list)  # [{by, at, comment}]
    policy: Mapped[dict | None] = mapped_column(JSONValue)  # snapshot of the policy when it was planned
    fingerprint: Mapped[str] = mapped_column(String(64), default="")  # of the plan; a re-plan with the same keeps approvals
    replaces: Mapped[int | None] = mapped_column()  # the plan this one superseded
    apply_after: Mapped[datetime | None] = mapped_column()  # approved: not before (delay, change window)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("job_template.id", ondelete="SET NULL"))

    integration: Mapped[Integration | None] = relationship()
    hosts: Mapped[list[JobHost]] = relationship(cascade="all, delete-orphan", order_by="JobHost.host")


class JobHost(db.Model):
    """Which hosts a job ran for: the run history of a host."""

    job_id: Mapped[int] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), primary_key=True)
    host: Mapped[str] = mapped_column(String(63), primary_key=True, index=True)


class ApprovalPolicy(db.Model):
    """Who must approve a plan before it applies, and when it may apply.
    The first enabled policy (by position) whose integrations, zones and triggers match decides."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    position: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    integrations: Mapped[list] = mapped_column(JSONList, default=lambda: ["*"])  # integration name globs
    zones: Mapped[list] = mapped_column(JSONList, default=lambda: ["*"])  # zone globs of the hosts a job runs for
    triggers: Mapped[list] = mapped_column(JSONList, default=lambda: ["manual", "hook", "schedule"])
    approvals: Mapped[int] = mapped_column(default=1)  # 0 = applies without anyone
    approvers: Mapped[list] = mapped_column(JSONList, default=list)  # usernames and @groups; empty = anyone who may apply
    self_approval: Mapped[bool] = mapped_column(default=True)  # the one who started the plan may approve it
    delay_minutes: Mapped[int] = mapped_column(default=0)  # applies no sooner than this after planning
    window_id: Mapped[int | None] = mapped_column(ForeignKey("change_window.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(200), default="")

    window: Mapped[ChangeWindow | None] = relationship()


class ChangeWindow(db.Model):
    """Weekly times when approved plans may apply, e.g. Tue and Thu 20:00–22:00 Europe/Berlin."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    days: Mapped[list] = mapped_column(JSONList, default=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    start: Mapped[str] = mapped_column(String(5), default="00:00")  # HH:MM
    end: Mapped[str] = mapped_column(String(5), default="00:00")  # HH:MM; at or before start = into the next day
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    description: Mapped[str] = mapped_column(String(200), default="")


class NotificationChannel(db.Model):
    """Where notifications go: ntfy, Matrix, email or a webhook, for the events chosen."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # ntfy, matrix, email, webhook
    enabled: Mapped[bool] = mapped_column(default=True)
    settings: Mapped[dict] = mapped_column(JSONValue, default=dict)  # secrets stored encrypted
    events: Mapped[list] = mapped_column(JSONList, default=list)  # event patterns, e.g. plan.waiting, job.failed, host.*
    integrations: Mapped[list] = mapped_column(JSONList, default=lambda: ["*"])  # only jobs of these integrations
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Notification(db.Model):
    """The outbox: one message for one channel, retried a few times."""

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("notification_channel.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")
    data: Mapped[dict] = mapped_column(JSONValue, default=dict)
    status: Mapped[str] = mapped_column(String(8), default="pending", index=True)  # pending, sent, failed
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    next_try_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column()

    channel: Mapped[NotificationChannel] = relationship()


class JobTemplate(db.Model):
    """An action with saved parameters (and host), run by a click or on a schedule."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integration.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(64))
    host: Mapped[str] = mapped_column(String(63), default="")  # for host actions
    params: Mapped[dict] = mapped_column(JSONValue, default=dict)
    description: Mapped[str] = mapped_column(String(200), default="")
    schedule: Mapped[str] = mapped_column(String(8), default="")  # '', every, weekly
    every_minutes: Mapped[int] = mapped_column(default=0)
    days: Mapped[list] = mapped_column(JSONList, default=list)
    at: Mapped[str] = mapped_column(String(5), default="")  # HH:MM (weekly)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None] = mapped_column()
    last_run_at: Mapped[datetime | None] = mapped_column()
    last_job_id: Mapped[int | None] = mapped_column()
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    integration: Mapped[Integration] = relationship()


class PluginRepository(db.Model):
    """A signed plugin index. Its key signs the index and every plugin in it; trusting the
    repository trusts that key."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    key_id: Mapped[str] = mapped_column(String(16))
    public_key: Mapped[str] = mapped_column(Text)  # base64 of the raw 32 bytes, pinned when added
    official: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    added_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    fetched_at: Mapped[datetime | None] = mapped_column()
    error: Mapped[str] = mapped_column(Text, default="")


class HostStatus(db.Model):
    """What one status source (a plugin instance) last said about a host."""

    __table_args__ = (UniqueConstraint("host", "integration_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(63), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integration.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(8), default="unknown")  # up, down, unknown
    detail: Mapped[str] = mapped_column(String(300), default="")
    data: Mapped[dict] = mapped_column(JSONValue, default=dict)  # small numbers for widgets (cpu, mem, …)
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    changed_at: Mapped[datetime] = mapped_column(default=utcnow)

    integration: Mapped[Integration] = relationship()


class HostHealth(db.Model):
    """The combined state of a host, kept to notice changes (host.down / host.up)."""

    host: Mapped[str] = mapped_column(String(63), primary_key=True)
    state: Mapped[str] = mapped_column(String(8), default="unknown")
    since: Mapped[datetime] = mapped_column(default=utcnow)


class ZonePolicy(db.Model):
    """One cell of the zone matrix: hosts in `src` may reach hosts in `dst` on these ports.
    No cell = no access (deny unless listed). Ports: "any", port aliases (SG_…) or tcp/443, udp/53."""

    __table_args__ = (UniqueConstraint("src", "dst"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    src: Mapped[str] = mapped_column(String(48))
    dst: Mapped[str] = mapped_column(String(48))
    ports: Mapped[list] = mapped_column(JSONList, default=list)
    description: Mapped[str] = mapped_column(String(200), default="")


class Worker(db.Model):
    """A process that runs jobs (the web server itself, or `labdex worker`)."""

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="inline")  # inline, external
    version: Mapped[str] = mapped_column(String(32), default="")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    running: Mapped[int] = mapped_column(default=0)


# =================================================================== discovery
class DiscoveredService(db.Model):
    __table_args__ = (UniqueConstraint("ip", "port", "proto"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(45), index=True)
    port: Mapped[int] = mapped_column()
    proto: Mapped[str] = mapped_column(String(8), default="tcp")
    state: Mapped[str] = mapped_column(String(16), default="open")
    service: Mapped[str] = mapped_column(String(64), default="")
    product: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)


class DiscoveredHost(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(45), unique=True)
    mac: Mapped[str] = mapped_column(String(17), default="")
    mac_source: Mapped[str] = mapped_column(String(64), default="")  # where the MAC came from (arp, kea, nmap)
    hostname: Mapped[str] = mapped_column(String(253), default="")
    vendor: Mapped[str] = mapped_column(String(128), default="")
    up: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)
