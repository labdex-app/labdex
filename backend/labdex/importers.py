"""Bring an inventory in from NetBox, phpIPAM or a CSV file, without replacing yours.

Each source is turned into the same plan, {networks, hosts, warnings}, in the shape of labdex's own
YAML; run() then adds what isn't there yet. Nothing is ever overwritten or deleted: a network or
host that already exists (by name, and for networks also by VLAN or prefix) is left as it is and
listed as skipped. Every created object is a History entry, so it can be reverted one by one.

Zones are the one thing the other tools don't have: everything comes in under the zone you
choose (or a zone per network with `zone_map`), and the Lint page and Hosts filters show what's
left to sort out. Only IPv4 addresses are brought in for now; IPv6 ones are counted in the warnings.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import re

import requests

from . import audit, naming, store
from .models import Host, Network, Setting, db

STATUS = {"active": "active", "planned": "planned", "staged": "planned", "inventory": "planned", "offline": "offline",
          "failed": "offline", "decommissioning": "legacy", "retired": "legacy", "legacy": "legacy"}
MAX_PAGES = 200


# ------------------------------------------------------------------ names
def slug(text: str, limit: int = 32) -> str:
    """A network name: lowercase letters, digits and dashes, starting with a letter."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    if s and not s[0].isalpha():
        s = "n" + s
    return s[:limit].rstrip("-")


def hostname(text: str) -> str:
    """A DNS label from a device name ('Core Switch.lab.example' → 'core-switch')."""
    s = re.sub(r"[^a-z0-9-]+", "-", str(text or "").lower().split(".")[0]).strip("-")
    return s[:63].strip("-")


def _ip4(text) -> str:
    """The address of '10.0.0.5/24' or '10.0.0.5', or '' (not IPv4)."""
    try:
        a = ipaddress.ip_interface(str(text or "").strip())
    except ValueError:
        return ""
    return str(a.ip) if a.version == 4 else ""


def _is_v6(text) -> bool:
    return ":" in str(text or "")


def _tags(raw) -> list[str]:
    out = []
    for t in raw or []:
        v = t.get("slug") or t.get("name") if isinstance(t, dict) else t
        v = re.sub(r"[^a-z0-9_.:-]+", "-", str(v or "").lower()).strip("-")[:32]
        if v:
            out.append(v)
    return sorted(set(out))


def empty() -> dict:
    return {"networks": [], "hosts": [], "warnings": []}


# ------------------------------------------------------------------ CSV
HOST_COLUMNS = {"name": "name", "hostname": "name", "host": "name", "fun": "fun", "ip": "ip", "ip4": "ip", "address": "ip", "mac": "mac",
                "network": "network", "zone": "zone", "function": "function", "role": "function", "status": "status", "tags": "tags",
                "hardware": "hardware", "vendor": "vendor", "model": "model", "notes": "notes", "description": "notes"}
NET_COLUMNS = {"name": "name", "network": "name", "prefix": "prefix", "subnet": "prefix", "cidr": "prefix", "vlan": "vlan", "gateway": "gateway",
               "zone": "zone", "notes": "notes", "description": "notes"}


def _rows(text: str, columns: dict) -> tuple[list[dict], list[str]]:
    text = (text or "").lstrip("﻿")
    if not text.strip():
        return [], ["the file is empty"]
    try:
        dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    head = {h: columns[h.strip().lower()] for h in reader.fieldnames or [] if h and h.strip().lower() in columns}
    ignored = [h for h in reader.fieldnames or [] if h and h not in head]
    rows = []
    for raw in reader:
        row = {head[k]: (v or "").strip() for k, v in raw.items() if k in head}
        if any(row.values()):
            rows.append(row)
    return rows, ([f"columns not used: {', '.join(ignored)}"] if ignored else [])


def from_csv(text: str, kind: str = "hosts") -> dict:
    """kind 'hosts': name, ip, mac, network, zone, function, status, tags, … (one address per row; a host
    on several rows gets several interfaces). kind 'networks': name, prefix, vlan, gateway, zone, notes."""
    plan = empty()
    if kind == "networks":
        rows, notes = _rows(text, NET_COLUMNS)
        plan["warnings"] += notes
        for r in rows:
            if not r.get("prefix"):
                plan["warnings"].append(f"network {r.get('name') or '?'}: no prefix, skipped")
                continue
            net = {"name": slug(r.get("name") or r["prefix"]), "prefix4": r["prefix"], "notes": r.get("notes", "")}
            if r.get("vlan"):
                net["vlan"] = r["vlan"]
            if r.get("gateway"):
                net["gateway4"] = r["gateway"]
            if r.get("zone"):
                net["zone"] = r["zone"]
            plan["networks"].append(net)
        return plan
    rows, notes = _rows(text, HOST_COLUMNS)
    plan["warnings"] += notes
    hosts: dict[str, dict] = {}
    for r in rows:
        name = hostname(r.get("name", ""))
        if not name:
            plan["warnings"].append("a row without a usable name was skipped")
            continue
        h = hosts.setdefault(name, {"name": name, "interfaces": [], "tags": []})
        for k in ("fun", "function", "hardware", "vendor", "model", "notes", "zone"):
            if r.get(k) and not h.get(k):
                h[k] = r[k]
        if r.get("status"):
            h["status"] = STATUS.get(r["status"].lower(), "active")
        h["tags"] = sorted(set(h["tags"]) | set(_tags(re.split(r"[,\s]+", r["tags"])) if r.get("tags") else []))
        ip = _ip4(r.get("ip"))
        if r.get("ip") and not ip:
            plan["warnings"].append(f"{name}: {r['ip']} isn't an IPv4 address, skipped")
        if ip or r.get("mac"):
            h["interfaces"].append({k: v for k, v in {"ip4": ip, "mac": r.get("mac", ""), "network": r.get("network", "")}.items() if v})
    plan["hosts"] = list(hosts.values())
    return plan


# ------------------------------------------------------------------ NetBox
def from_netbox(data: dict) -> dict:
    """data: the lists NetBox's REST API gives: prefixes, vlans, devices, virtual_machines, ip_addresses, interfaces."""
    plan = empty()
    for p in data.get("prefixes", []):
        cidr = str(p.get("prefix") or "")
        if _is_v6(cidr):
            plan["warnings"].append(f"IPv6 prefix {cidr} not imported")
            continue
        vlan = p.get("vlan") or {}
        name = slug(vlan.get("name") or p.get("description") or cidr.replace("/", "-").replace(".", "-"))
        net = {"name": name, "prefix4": cidr, "notes": str(p.get("description") or "")[:200]}
        if vlan.get("vid"):
            net["vlan"] = vlan["vid"]
        plan["networks"].append(net)

    macs: dict[tuple[str, str], str] = {}
    for i in data.get("interfaces", []):
        owner = ((i.get("device") or i.get("virtual_machine")) or {}).get("name", "")
        mac = i.get("mac_address") or (i.get("primary_mac_address") or {}).get("mac_address") or ""
        if owner and mac:
            macs[(owner, i.get("name", ""))] = mac.lower()

    by_host: dict[str, dict] = {}

    def host_for(name: str, src: dict | None = None) -> dict | None:
        key = hostname(name)
        if not key:
            return None
        h = by_host.get(key)
        if h is None:
            h = by_host[key] = {"name": key, "interfaces": [], "tags": []}
        if src is not None:
            role = src.get("role") or src.get("device_role") or {}
            h.setdefault("function", (role.get("name") or src.get("description") or "")[:200])
            dt = src.get("device_type") or {}
            if dt:
                h.setdefault("vendor", ((dt.get("manufacturer") or {}).get("name") or "")[:100])
                h.setdefault("model", (dt.get("model") or "")[:100])
            h["status"] = STATUS.get(str((src.get("status") or {}).get("value", "active")).lower(), "active")
            h["tags"] = _tags(src.get("tags"))
            notes = str(src.get("comments") or "")[:500]
            h["notes"] = (f"NetBox name: {src['name']}\n{notes}" if src.get("name") != key else notes).strip()
        return h

    for d in data.get("devices", []) + data.get("virtual_machines", []):
        host_for(d.get("name", ""), d)
    v6 = 0
    for a in data.get("ip_addresses", []):
        addr = str(a.get("address") or "")
        if _is_v6(addr):
            v6 += 1
            continue
        ip = _ip4(addr)
        obj = a.get("assigned_object") or {}
        owner = (obj.get("device") or obj.get("virtual_machine") or {}).get("name", "")
        h = host_for(owner) if owner else None
        if not ip or h is None:
            continue
        iface = {"ip4": ip, "label": str(obj.get("name") or "")[:32]}
        mac = macs.get((owner, obj.get("name", "")))
        if mac:
            iface["mac"] = mac
        h["interfaces"].append(iface)
    for d in data.get("devices", []) + data.get("virtual_machines", []):
        h = by_host.get(hostname(d.get("name", "")))
        pip = _ip4((d.get("primary_ip4") or {}).get("address"))
        if h is not None and pip and not any(i["ip4"] == pip for i in h["interfaces"]):
            h["interfaces"].append({"ip4": pip, "label": "primary"})
        if h is not None and pip:
            for i in h["interfaces"]:
                i["primary"] = i["ip4"] == pip
    if v6:
        plan["warnings"].append(f"{v6} IPv6 address(es) not imported")
    plan["hosts"] = list(by_host.values())
    return plan


def fetch_netbox(url: str, token: str, verify: bool = True, session=None) -> dict:
    """Read prefixes, devices, virtual machines, addresses and interfaces from NetBox's REST API (read-only token)."""
    base = url.rstrip("/")
    s = session or requests.Session()
    s.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})
    out = {}
    for key, path in (("prefixes", "ipam/prefixes"), ("devices", "dcim/devices"), ("virtual_machines", "virtualization/virtual-machines"),
                      ("ip_addresses", "ipam/ip-addresses"), ("interfaces", "dcim/interfaces")):
        rows, nxt, pages = [], f"{base}/api/{path}/?limit=500", 0
        while nxt and pages < MAX_PAGES:
            r = s.get(nxt, timeout=30, verify=verify)
            if r.status_code in (401, 403):
                raise store.ValidationError([{"field": "token", "message": "NetBox refused the token (it needs read access)"}])
            r.raise_for_status()
            j = r.json()
            rows += j.get("results", [])
            nxt, pages = j.get("next"), pages + 1
        out[key] = rows
    return out


# ------------------------------------------------------------------ phpIPAM
def from_phpipam(data: dict) -> dict:
    """data: subnets, vlans and addresses ({subnet_id: [...]}) as phpIPAM's REST API gives them."""
    plan = empty()
    vlans = {str(v.get("vlanId")): v for v in data.get("vlans", [])}
    for sn in data.get("subnets", []):
        base, mask = str(sn.get("subnet") or ""), str(sn.get("mask") or "")
        if sn.get("isFolder") in ("1", 1, True) or not base or not mask:
            continue
        cidr = f"{base}/{mask}"
        if _is_v6(base):
            plan["warnings"].append(f"IPv6 subnet {cidr} not imported")
            continue
        vlan = vlans.get(str(sn.get("vlanId") or ""))
        name = slug((vlan or {}).get("name") or sn.get("description") or cidr.replace("/", "-").replace(".", "-"))
        net = {"name": name, "prefix4": cidr, "notes": str(sn.get("description") or "")[:200]}
        if vlan and str(vlan.get("number") or "").isdigit() and int(vlan["number"]) > 0:
            net["vlan"] = int(vlan["number"])
        if sn.get("gateway") and isinstance(sn["gateway"], dict) and sn["gateway"].get("ip_addr"):
            net["gateway4"] = sn["gateway"]["ip_addr"]
        plan["networks"].append(net)
    by_host: dict[str, dict] = {}
    for rows in (data.get("addresses") or {}).values():
        for a in rows or []:
            ip, name = _ip4(a.get("ip")), hostname(a.get("hostname") or "")
            if not ip or not name:
                continue
            h = by_host.setdefault(name, {"name": name, "interfaces": [], "tags": [], "status": "active"})
            if a.get("description") and not h.get("function"):
                h["function"] = str(a["description"])[:200]
            iface = {"ip4": ip}
            if a.get("mac"):
                iface["mac"] = str(a["mac"]).lower().replace("-", ":")
            h["interfaces"].append(iface)
    plan["hosts"] = list(by_host.values())
    return plan


def fetch_phpipam(url: str, app_id: str, username: str, password: str, verify: bool = True, session=None) -> dict:
    """url is the phpIPAM site (https://ipam.example); app_id the API application's id (security: user token)."""
    base = f"{url.rstrip('/')}/api/{app_id}"
    s = session or requests.Session()
    r = s.post(f"{base}/user/", auth=(username, password), timeout=30, verify=verify)
    if r.status_code in (401, 403) or not (r.json().get("data") or {}).get("token"):
        raise store.ValidationError([{"field": "password", "message": "phpIPAM refused the sign-in"}])
    s.headers["token"] = r.json()["data"]["token"]

    def get(path):
        resp = s.get(f"{base}/{path}/", timeout=30, verify=verify)
        if resp.status_code == 404:  # phpIPAM answers 404 for "nothing here"
            return []
        resp.raise_for_status()
        return resp.json().get("data") or []

    subnets = get("subnets")
    return {"subnets": subnets, "vlans": get("vlan"),
            "addresses": {str(sn["id"]): get(f"subnets/{sn['id']}/addresses") for sn in subnets if sn.get("id") and sn.get("subnet") and not _is_v6(sn["subnet"])}}


def build_plan(data: dict) -> dict:
    """The plan for a request body: {"source": "csv" | "netbox" | "phpipam", …} (see the admin API). May read from the network."""
    src = str(data.get("source") or "")
    verify = data.get("verify_tls", True) is not False

    def need(*keys):
        missing = [k for k in keys if not data.get(k)]
        if missing:
            raise store.ValidationError([{"field": k, "message": "required"} for k in missing])

    if src == "csv":
        if not (data.get("networks_text") or data.get("hosts_text")):
            raise store.ValidationError([{"field": "hosts_text", "message": "paste or upload a CSV"}])
        nets = from_csv(str(data.get("networks_text") or ""), "networks") if data.get("networks_text") else empty()
        hosts = from_csv(str(data.get("hosts_text") or ""), "hosts") if data.get("hosts_text") else empty()
        return {"networks": nets["networks"], "hosts": hosts["hosts"], "warnings": nets["warnings"] + hosts["warnings"]}
    if src == "netbox":
        need("url", "token")
        return from_netbox(fetch_netbox(str(data["url"]), str(data["token"]), verify))
    if src == "phpipam":
        need("url", "app_id", "username", "password")
        return from_phpipam(fetch_phpipam(str(data["url"]), str(data["app_id"]), str(data["username"]), str(data["password"]), verify))
    raise store.ValidationError([{"field": "source", "message": "csv, netbox or phpipam"}])


# ------------------------------------------------------------------ apply
def _networks_by_prefix() -> list[tuple[ipaddress.IPv4Network, str]]:
    out = []
    for n in db.session.scalars(db.select(Network)):
        for p in n.prefixes:
            try:
                if p.family == 4 and p.prefix:
                    out.append((ipaddress.ip_network(p.prefix, strict=False), n.name))
            except ValueError:
                pass
    return out


def _owner(known, ip: str) -> str:
    """The most specific network that holds the address, or ''."""
    best = None
    for net, name in known:
        if ipaddress.ip_address(ip) in net and (best is None or net.prefixlen > best[0].prefixlen):
            best = (net, name)
    return best[1] if best else ""


def run(plan: dict, zone: str, zone_map: dict | None = None, apply: bool = False) -> dict:
    """Add what the plan holds and isn't there yet. Without apply nothing is kept (a preview).
    Returns {created: {networks, hosts}, skipped: [...], failed: [...], warnings: [...]}."""
    zone = (zone or "").upper()
    zone_map = {str(k).lower(): str(v).upper() for k, v in (zone_map or {}).items()}
    if naming.parse_zone(zone) is None:
        hint = "create zones under Settings › Zones first (or load the example set)" if not naming.all_zones() else "pick one of the zones"
        raise store.ValidationError([{"field": "zone", "message": f"unknown zone '{zone}': {hint}"}])
    bad = sorted({z for z in zone_map.values() if naming.parse_zone(z) is None})
    if bad:
        raise store.ValidationError([{"field": "zone_map", "message": f"unknown zone '{bad[0]}'"}])

    out = {"created": {"networks": [], "hosts": []}, "skipped": [], "failed": [], "warnings": list(plan.get("warnings", []))}
    # SQLite's driver only begins a transaction at the first write; a SAVEPOINT released before that would commit
    # everything. A write that changes nothing makes the whole run one transaction, so a preview can be rolled back.
    db.session.execute(db.update(Setting).where(Setting.key == "").values(value=""))
    taken_vlans = {n.vlan for n in db.session.scalars(db.select(Network)) if n.vlan}
    existing_prefixes = {str(net) for net, _ in _networks_by_prefix()}
    try:
        for n in plan.get("networks", []):
            name = n.get("name") or ""
            cidr = n.get("prefix4", "")
            try:
                canon = str(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                out["failed"].append({"kind": "network", "name": name, "errors": [f"'{cidr}' is not a prefix"]})
                continue
            if canon in existing_prefixes:
                out["skipped"].append({"kind": "network", "name": name, "why": f"{canon} is already in labdex"})
                continue
            data = dict(n, zone=(n.get("zone") or zone_map.get(name) or zone).upper())
            if data.get("vlan") and int(data["vlan"]) in taken_vlans:
                out["warnings"].append(f"network {name}: VLAN {data['vlan']} is already used, imported without a VLAN")
                data.pop("vlan")
            base, i = name or slug(canon), 1
            while store.find_network(data.get("name") or base) is not None:  # same name, different prefix
                i += 1
                data["name"] = f"{base[:28]}-{i}"
            data.setdefault("name", base)
            try:
                with db.session.begin_nested():
                    net = store.save_network(data)
                    db.session.flush()
                    audit.record("create", "network", net.name, None, store.network_to_dict(net), summary="imported")
            except store.ValidationError as exc:
                out["failed"].append({"kind": "network", "name": name, "errors": [f"{e['field']}: {e['message']}" for e in exc.errors]})
                continue
            existing_prefixes.add(canon)
            if net.vlan:
                taken_vlans.add(net.vlan)
            out["created"]["networks"].append(net.name)

        known = _networks_by_prefix()
        used_ips = {i.ip4 for h in db.session.scalars(db.select(Host)) for i in h.interfaces if i.ip4}
        for h in plan.get("hosts", []):
            name = h["name"]
            if store.find_host(name) is not None:
                out["skipped"].append({"kind": "host", "name": name, "why": "a host with that name exists"})
                continue
            ifaces, home = [], ""
            for i in h.get("interfaces", []):
                iface = dict(i)
                if iface.get("ip4"):
                    if iface["ip4"] in used_ips:
                        out["warnings"].append(f"{name}: {iface['ip4']} is already used by another host, address skipped")
                        continue
                    net = iface.get("network") or _owner(known, iface["ip4"])
                    if not net:
                        out["warnings"].append(f"{name}: {iface['ip4']} is in no network labdex has, address skipped")
                        continue
                    iface["network"] = net
                    home = home or net
                ifaces.append(iface)
            if ifaces and not any(i.get("primary") for i in ifaces):
                ifaces[0]["primary"] = True
            data = {k: v for k, v in h.items() if k not in ("interfaces", "zone")}
            data["interfaces"] = ifaces
            data["zone"] = (h.get("zone") or zone_map.get(home) or zone).upper()
            try:
                with db.session.begin_nested():
                    host = store.save_host(data)
                    db.session.flush()
                    audit.record("create", "host", host.name, None, store.host_to_dict(host), summary="imported")
            except store.ValidationError as exc:
                out["failed"].append({"kind": "host", "name": name, "errors": [f"{e['field']}: {e['message']}" for e in exc.errors]})
                continue
            used_ips |= {i["ip4"] for i in ifaces if i.get("ip4")}
            out["created"]["hosts"].append(host.name)
        if apply:
            db.session.commit()
        else:
            db.session.rollback()
    except Exception:
        db.session.rollback()
        raise
    return out
