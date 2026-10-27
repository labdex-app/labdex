"""Neighbours from LLDP-MIB and CISCO-CDP-MIB walks: who is on which port.

Each function takes walk results ([(oid, value)]) and gives back plain dicts:
    {"port": local port name, "protocol": "lldp"|"cdp", "name": remote system name,
     "remote_port": remote port name, "mac": remote chassis MAC, "ip": remote management address,
     "platform": "", "description": remote port description}
"""
from __future__ import annotations

import ipaddress

SYS_NAME = "1.3.6.1.2.1.1.5.0"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"  # .ifIndex
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"  # .ifIndex
LLDP_LOC_PORT = "1.0.8802.1.1.2.1.3.7.1"  # .3 id subtype? (.2 subtype, .3 id, .4 desc) .portNum
LLDP_REM = "1.0.8802.1.1.2.1.4.1.1"  # .column.timeMark.portNum.index
LLDP_REM_MAN = "1.0.8802.1.1.2.1.4.2.1"  # .column.timeMark.portNum.index.addrSubtype.len.addr…
CDP_CACHE = "1.3.6.1.4.1.9.9.23.1.2.1.1"  # .column.ifIndex.deviceIndex
# forwarding tables: which MAC is behind which port
BRIDGE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"  # dot1dBasePortIfIndex.bridgePort = ifIndex
BRIDGE_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"  # dot1dTpFdbPort.<6 MAC octets> = bridgePort
BRIDGE_FDB_STATUS = "1.3.6.1.2.1.17.4.3.1.3"  # .<mac> = 3 learned, 4 self, 5 mgmt
QBRIDGE_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"  # dot1qTpFdbPort.<fdbId>.<6 MAC octets> = bridgePort
QBRIDGE_FDB_STATUS = "1.3.6.1.2.1.17.7.1.2.2.1.3"
QBRIDGE_VLAN_FDB = "1.3.6.1.2.1.17.7.1.4.2.1.3"  # dot1qVlanFdbId.<timeMark>.<vlan> = fdbId
WALKS = {"if_name": IF_NAME, "if_descr": IF_DESCR, "lldp_loc": LLDP_LOC_PORT, "lldp_rem": LLDP_REM,
         "lldp_man": LLDP_REM_MAN, "cdp": CDP_CACHE, "bridge_ports": BRIDGE_PORT_IFINDEX, "fdb": BRIDGE_FDB_PORT,
         "fdb_status": BRIDGE_FDB_STATUS, "qfdb": QBRIDGE_FDB_PORT, "qfdb_status": QBRIDGE_FDB_STATUS, "vlan_fdb": QBRIDGE_VLAN_FDB}


def text(v) -> str:
    if isinstance(v, bytes):
        try:
            s = v.decode("utf-8")
        except UnicodeDecodeError:
            return ""
        s = s.strip("\x00").strip()
        return s if s.isprintable() else ""
    return "" if v is None else str(v)


def mac(v) -> str:
    if isinstance(v, bytes) and len(v) == 6:
        return ":".join(f"{b:02x}" for b in v)
    t = text(v).lower().replace("-", ":").replace(".", "")
    if len(t) == 12 and all(c in "0123456789abcdef" for c in t):  # aabb.ccdd.eeff (Cisco)
        return ":".join(t[i:i + 2] for i in range(0, 12, 2))
    return t if t.count(":") == 5 else ""


def _rows(walk, base: str) -> dict[tuple, dict[int, object]]:
    """{index tuple: {column: value}} for a table walked from its entry OID."""
    out: dict[tuple, dict[int, object]] = {}
    plen = len(base.split("."))
    for oid, val in walk:
        parts = oid.split(".")
        if len(parts) <= plen + 1:
            continue
        col, idx = int(parts[plen]), tuple(int(x) for x in parts[plen + 1:])
        out.setdefault(idx, {})[col] = val
    return out


def if_names(if_name_walk, if_descr_walk=()) -> dict[int, str]:
    names = {int(o.rsplit(".", 1)[1]): text(v) for o, v in if_descr_walk}
    names.update({int(o.rsplit(".", 1)[1]): text(v) for o, v in if_name_walk if text(v)})
    return names


def _port_id(subtype, value, desc) -> str:
    """A port's name from LLDP's port id: interface name (5), local (7) and alias (1) are names;
    a MAC (3) or network address (4) isn't, then the description is better."""
    if subtype in (1, 5, 7):
        return text(value) or text(desc)
    if subtype == 3:
        return text(desc) or mac(value)
    return text(desc) or text(value)


def lldp(rem_walk, loc_walk=(), man_walk=(), ifnames: dict[int, str] | None = None) -> list[dict]:
    ifnames = ifnames or {}
    loc = {idx[0]: cols for idx, cols in _rows(loc_walk, LLDP_LOC_PORT).items() if idx}
    addrs: dict[tuple, str] = {}
    for oid, _ in man_walk:  # the address is in the index: .col.time.port.idx.subtype.len.bytes…
        parts = [int(x) for x in oid.split(".")[len(LLDP_REM_MAN.split(".")):]]
        if len(parts) < 6:
            continue
        key, subtype, n, raw = tuple(parts[1:4]), parts[4], parts[5], parts[6:]
        if subtype == 1 and n == 4 and len(raw) == 4:
            addrs.setdefault(key, str(ipaddress.IPv4Address(bytes(raw))))
        elif subtype == 2 and n == 16 and len(raw) == 16:
            addrs.setdefault(key, str(ipaddress.IPv6Address(bytes(raw))))
    out = []
    for idx, c in sorted(_rows(rem_walk, LLDP_REM).items()):
        if len(idx) < 3:
            continue
        _, port_num, _ = idx[:3]
        lp = loc.get(port_num, {})
        local = (_port_id(lp.get(2), lp.get(3), lp.get(4)) if lp else "") or ifnames.get(port_num, "") or str(port_num)
        chassis_sub, chassis = c.get(4), c.get(5)
        out.append({
            "port": local, "protocol": "lldp", "name": text(c.get(9)).split(".")[0] if text(c.get(9)) else "",
            "fqdn": text(c.get(9)), "remote_port": _port_id(c.get(6), c.get(7), c.get(8)),
            "mac": mac(chassis) if chassis_sub == 4 else "", "ip": addrs.get(idx[:3], ""),
            "description": text(c.get(8)), "platform": text(c.get(10))[:120],
        })
    return out


def cdp(cache_walk, ifnames: dict[int, str]) -> list[dict]:
    out = []
    for idx, c in sorted(_rows(cache_walk, CDP_CACHE).items()):
        if len(idx) < 2:
            continue
        ip = ""
        a = c.get(4)
        if isinstance(a, bytes) and len(a) == 4 and c.get(3) == 1:
            ip = str(ipaddress.IPv4Address(a))
        dev = text(c.get(6))
        out.append({"port": ifnames.get(idx[0], str(idx[0])), "protocol": "cdp", "name": dev.split("(")[0].split(".")[0],
                    "fqdn": dev, "remote_port": text(c.get(7)), "mac": "", "ip": ip, "description": "",
                    "platform": text(c.get(8))[:120]})
    return out


def _tail(oid: str, base: str) -> list[int]:
    o = oid.lstrip(".")
    if not o.startswith(base + "."):
        return []
    try:
        return [int(x) for x in o[len(base) + 1:].split(".")]
    except ValueError:
        return []


def fdb(bridge_ports=(), fdb_walk=(), fdb_status=(), qfdb_walk=(), qfdb_status=(), vlan_fdb=(), ifnames: dict[int, str] | None = None) -> list[dict]:
    """The switch's forwarding table: [{"mac", "port", "vlan"}] for learned addresses.

    Q-BRIDGE-MIB (per VLAN) when the switch has it, else BRIDGE-MIB. Bridge port numbers are
    turned into interface names through dot1dBasePortIfIndex and ifName. The switch's own
    addresses (status self/mgmt) are left out."""
    ifnames = ifnames or {}
    port_if = {}
    for oid, v in bridge_ports:
        t = _tail(oid, BRIDGE_PORT_IFINDEX)
        if len(t) == 1:
            try:
                port_if[t[0]] = int(v)
            except (TypeError, ValueError):
                pass

    def name(bport) -> str:
        try:
            b = int(bport)
        except (TypeError, ValueError):
            return ""
        if b <= 0:
            return ""
        ifx = port_if.get(b, b)
        return ifnames.get(ifx) or str(ifx)

    fid_vlan = {}
    for oid, v in vlan_fdb:
        t = _tail(oid, QBRIDGE_VLAN_FDB)
        if len(t) == 2:
            try:
                fid_vlan[int(v)] = t[1]
            except (TypeError, ValueError):
                pass
    out, seen = [], set()

    def status_of(walk, base, size):
        st = {}
        for oid, v in walk:
            t = _tail(oid, base)
            if len(t) == size:
                try:
                    st[tuple(t)] = int(v)
                except (TypeError, ValueError):
                    pass
        return st

    qst = status_of(qfdb_status, QBRIDGE_FDB_STATUS, 7)
    for oid, v in qfdb_walk:
        t = _tail(oid, QBRIDGE_FDB_PORT)
        if len(t) != 7 or qst.get(tuple(t), 3) not in (3,):
            continue
        m = ":".join(f"{b:02x}" for b in t[1:])
        p = name(v)
        if p and (m, p) not in seen:
            seen.add((m, p))
            out.append({"mac": m, "port": p, "vlan": fid_vlan.get(t[0], t[0] if 1 <= t[0] <= 4094 else None)})  # an FDB id is often the VLAN
    if out:
        return out
    st = status_of(fdb_status, BRIDGE_FDB_STATUS, 6)
    for oid, v in fdb_walk:
        t = _tail(oid, BRIDGE_FDB_PORT)
        if len(t) != 6 or st.get(tuple(t), 3) not in (3,):
            continue
        m = ":".join(f"{b:02x}" for b in t)
        p = name(v)
        if p and (m, p) not in seen:
            seen.add((m, p))
            out.append({"mac": m, "port": p, "vlan": None})
    return out
