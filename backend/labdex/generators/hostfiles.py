"""Per-host outputs: Ansible inventory, DHCP reservations and the login banner."""
from __future__ import annotations

import csv
import io
import re

import yaml

from .. import naming
from ..models import Host, db
from .aliases import host_aliases

ANSI = {"magenta": "35", "green": "32", "yellow": "33", "blue": "34", "red": "31", "white": "37"}


def _hosts(include_offline=False):
    q = db.select(Host).order_by(Host.name)
    if not include_offline:
        q = q.where(Host.status != "offline")
    return db.session.scalars(q).all()


# ------------------------------------------------------------------- MOTD
def motd(host: Host) -> str:
    """Plain-text login banner (no colour codes; the prompt carries the colour)."""
    z = naming.parse_zone(host.zone)
    exposure = "internet-facing" if z and z.area == naming.get_setting("dmz_area") else "internal"
    ips = "  ".join(
        f"{i.network.name if i.network else (i.label or '?')} {' '.join(i.addresses)}"
        for i in host.interfaces if i.addresses
    )
    title = f"{host.fun}  ·  {host.name}" if host.fun else host.name
    rows = [
        ("Function", host.function or "—"),
        ("Zone", f"{host.zone}  ({naming.zone_long(host.zone)})"),
        ("Exposure", exposure),
        ("IPs", ips or "—"),
    ]
    if host.runs_on:
        rows.append(("Runs on", host.runs_on.name))
    rows.append(("Managed", "labdex inventory: changes made here may be overwritten"))
    width = max(len(title), 45)
    lines = ["", f"  {title}", "  " + "─" * width]
    lines += [f"  {k:<9} {v}" for k, v in rows]
    return "\n".join(lines) + "\n\n"


def prompt(host: Host) -> str:
    """bash PS1 with the zone colour, e.g. mike@arcanine [net-fw-edge-01 · DC_HT] ~ $"""
    code = ANSI[naming.zone_colour(host.zone)]
    who = host.fun or host.name
    return rf"\[\e[1;{code}m\]\u@{who}\[\e[0m\] [{host.name} · {host.zone}] \w \$ "


# ---------------------------------------------------------------- Ansible
def ansible_inventory() -> dict:
    from .. import features as feat
    feats = feat.all_features()
    groups: dict[str, dict] = {}
    hostvars: dict[str, dict] = {}
    vip_groups: dict[str, dict] = {}
    for h in _hosts():
        if h.is_vip:
            # not a machine to log in to: its members get it as a var and a group
            vip_groups[f"vip_{h.name.replace('-', '_')}"] = {"hosts": {m.name: None for m in h.vip_members}}
            continue
        p = naming.primary_interface(h)
        addr = p.addresses[0] if p else next((i.addresses[0] for i in h.interfaces if i.addresses), "")
        z = naming.parse_zone(h.zone)
        hv = {
            "labdex_fun": h.fun or "",
            "labdex_fun_names": h.fun_names,
            "labdex_zone": h.zone,
            "labdex_zone_long": naming.zone_long(h.zone),
            "labdex_status": h.status,
            "labdex_function": h.function,
            "labdex_role": h.function,  # deprecated name, removed in 0.7
            "labdex_runs_on": h.runs_on.name if h.runs_on else "",
            "labdex_tags": list(h.tags or []),
            "labdex_aliases": [a.name for a in host_aliases(h)],
            "labdex_interfaces": [
                {k: v for k, v in {
                    "network": i.network.name if i.network else "",
                    "label": i.label, "ip4": i.ip4, "ip6": i.ip6s[0] if i.ip6s else "", "ip6_all": i.ip6s, "mac": i.mac, "primary": i.primary,
                }.items() if v not in ("", None, False)}
                for i in h.interfaces
            ],
            "labdex_prompt_colour": naming.zone_colour(h.zone),
            "labdex_ps1": prompt(h),
            "labdex_motd": motd(h),
        }
        if h.vips:
            hv["labdex_vips"] = [{"name": v.name, "protocol": v.vip_protocol or "", "peers": [m.name for m in v.vip_members if m is not h],
                                  "addresses": [{k: x for k, x in {"network": i.network.name if i.network else "", "ip4": i.ip4, "ip6": i.ip6s[0] if i.ip6s else ""}.items() if x}
                                                for i in v.interfaces]} for v in h.vips]
        if addr:
            hv["ansible_host"] = addr
        if h.features:
            hv["labdex_features"] = feat.expanded(h.features)
        # feature vars (group_vars-like, nested) and the host's own extra vars win over labdex_*
        hv.update(feat.host_vars(h, feats))
        hostvars[h.name] = hv
        names = [f"cat_{h.name.split('-')[0]}" if naming.hostname_re().match(h.name) else "cat_unnamed",
                 f"zone_{h.zone.lower()}", f"status_{h.status}"]
        if z:
            names.append(f"area_{z.area.lower()}")
        names += [f"tag_{t}" for t in (h.tags or [])]
        names += ["feature_" + re.sub(r"[^a-z0-9_]", "_", p.replace(".", "__")) for p in feat.expanded(h.features or [])]
        for g in names:
            groups.setdefault(g, {"hosts": {}})["hosts"][h.name] = None
    for g, members in vip_groups.items():
        members["hosts"] = {n: None for n in members["hosts"] if n in hostvars}
        if members["hosts"]:
            groups[g] = members
    return {"all": {"hosts": hostvars, "children": dict(sorted(groups.items()))}}


def ansible_yaml(vault_password: str = "", vault_id: str = "") -> str:
    """The inventory as YAML. Secret references ({$secret: name}) stay references,
    or become `!vault` values when a vault password is given."""
    head = "# Generated by labdex. Do not edit; change the inventory instead.\n"
    from .. import ansiblevault
    from ..inventory import _Dumper
    inv = ansible_inventory()
    if vault_password and ansiblevault.refs(inv):
        inv = ansiblevault.vault_all(inv, vault_password, vault_id)

        class D(_Dumper):
            pass
        D.add_representer(ansiblevault.Vaulted, lambda d, v: d.represent_scalar("!vault", str(v), style="|"))
        return head + yaml.dump(inv, Dumper=D, sort_keys=False, allow_unicode=True, width=120)
    return head + yaml.dump(inv, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


# ------------------------------------------------------------------- DHCP
def dhcp_reservations() -> list[dict]:
    out = []
    for h in _hosts(include_offline=True):
        for i in h.interfaces:
            if i.mac and i.ip4 and i.network:
                out.append({
                    "network": i.network.name, "vlan": i.network.vlan, "mac": i.mac, "ip": i.ip4,
                    "hostname": h.name, "description": h.fun or h.function,
                })
    return sorted(out, key=lambda r: (r["vlan"] if r["vlan"] is not None else 9999, tuple(int(x) for x in r["ip"].split("."))))


def dhcp_csv() -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["network", "vlan", "mac", "ip", "hostname", "description"])
    w.writeheader()
    w.writerows(dhcp_reservations())
    return buf.getvalue()
