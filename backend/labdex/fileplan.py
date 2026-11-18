"""What importing an inventory file would change: the plan a merge request shows before it's merged.

    plan = fileplan.plan(new_text, base_text=None, site=None)

Without a base, the file is compared with this labdex's inventory. With one (the files on the target
branch), the base is imported first, so CI can plan with an empty database. Nothing is kept: both
imports are rolled back. The plan lists the inventory objects that change (field by field) and what
labdex would generate differently: DNS records, firewall aliases and DHCP reservations.

`markdown(plan)` renders it for a merge request comment, `text(plan)` for a terminal.
"""
from __future__ import annotations

from . import inventory, naming, sites, store
from .audit import diff as field_diff
from .models import db

# sections of a full file whose items are told apart by a key; anything else is compared whole
KEYS = ("name", "code")
GENERATED = ("dns", "aliases", "dhcp")


def snapshot(site: str | None = None) -> dict:
    """The inventory (as the file would hold it) and what it generates, for one site or all."""
    from .generators import aliases, dns, hostfiles
    data = inventory.export_dict(site)
    data.pop("site", None)
    data.pop("site_name", None)
    domain = naming.get_setting("domain")
    with sites.scoped(site):
        recs = sorted({f"{r.fqdn(domain).rstrip('.')} {r.type} {r.value}" for r in dns.records()})
        als = {a.name: sorted(a.content) for a in aliases.all_aliases()}
        dhcp = sorted({f"{r['network']} {r['mac']} {r['ip']} {r['hostname']}" for r in hostfiles.dhcp_reservations()})
    return {"inventory": data, "dns": recs, "aliases": als, "dhcp": dhcp}


def _site_of(text: str, site: str | None) -> str | None:
    import yaml
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return site
    if not site and isinstance(data, dict) and isinstance(data.get("site"), str):
        return data["site"]
    return site or None


def plan(new_text: str, base_text: str | list[str] | None = None, site: str | None = None) -> dict:
    """The plan for importing `new_text` (over `base_text`, a file or files imported in order, or over
    this labdex). Never commits."""
    from .cache import bump
    site = _site_of(new_text, site)
    out = {"site": site or "", "ok": True, "errors": [], "inventory": [], "generated": {}, "summary": {}}
    try:
        for base in [base_text] if isinstance(base_text, str) else base_text or []:
            try:
                inventory.import_yaml(base, commit=False)
            except store.ValidationError as exc:
                out.update(ok=False, errors=[{"file": "base", **e} for e in exc.errors])
                return out
        before = snapshot(site)
        if site and not sites.exists(site):  # a new site comes with its first file
            before["inventory"] = {"networks": [], "hosts": []}
        try:
            inventory.import_yaml(new_text, commit=False, site=site)
        except store.ValidationError as exc:
            out.update(ok=False, errors=[{"file": "new", **e} for e in exc.errors])
            return out
        after = snapshot(site)
    finally:
        db.session.rollback()
        db.session.expire_all()
        bump()
    out["inventory"] = compare_inventory(before["inventory"], after["inventory"])
    out["generated"] = {
        "dns": _set_diff(before["dns"], after["dns"]),
        "dhcp": _set_diff(before["dhcp"], after["dhcp"]),
        "aliases": _alias_diff(before["aliases"], after["aliases"]),
    }
    ops = [c["op"] for c in out["inventory"]]
    out["summary"] = {"create": ops.count("create"), "update": ops.count("update"), "delete": ops.count("delete"),
                      "dns": sum(len(v) for v in out["generated"]["dns"].values()),
                      "aliases": len(out["generated"]["aliases"]),
                      "dhcp": sum(len(v) for v in out["generated"]["dhcp"].values())}
    return out


def _key(item) -> str | None:
    if isinstance(item, dict):
        for k in KEYS:
            if isinstance(item.get(k), str | int) and item.get(k) != "":
                return str(item[k])
    return None


def compare_inventory(before: dict, after: dict) -> list[dict]:
    """Changes between two exports, by section and object: [{op, kind, name, fields}]."""
    out = []
    for section in sorted(set(before) | set(after)):
        b, a = before.get(section), after.get(section)
        if b == a:
            continue
        kind = section[:-1] if section.endswith("s") and section not in ("settings", "naming") else section
        items_b, items_a = _by_key(b), _by_key(a)
        if items_b is None or items_a is None:  # a mapping, or a list without names: one change for the section
            out.append({"op": "update", "kind": section, "name": section, "fields": field_diff(_wrap(b), _wrap(a))})
            continue
        for name in sorted(set(items_b) | set(items_a)):
            ib, ia = items_b.get(name), items_a.get(name)
            if ib == ia:
                continue
            op = "create" if ib is None else "delete" if ia is None else "update"
            out.append({"op": op, "kind": kind, "name": name, "fields": field_diff(ib, ia) if op == "update" else []})
    return out


def _wrap(v):
    return v if isinstance(v, dict) else {"value": v}


def _by_key(items) -> dict | None:
    if items is None:
        return {}
    if not isinstance(items, list):
        return None
    keyed = {}
    for it in items:
        k = _key(it)
        if k is None or k in keyed:
            return None
        keyed[k] = it
    return keyed


def _set_diff(before: list, after: list) -> dict:
    b, a = set(before), set(after)
    return {"add": sorted(a - b), "remove": sorted(b - a)}


def _alias_diff(before: dict, after: dict) -> list[dict]:
    out = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b == a:
            continue
        op = "create" if b is None else "delete" if a is None else "update"
        out.append({"op": op, "name": name, "add": sorted(set(a or []) - set(b or [])), "remove": sorted(set(b or []) - set(a or []))})
    return out


def empty(p: dict) -> bool:
    return p["ok"] and not p["inventory"] and not any(
        (v["add"] or v["remove"]) if isinstance(v, dict) else v for v in p["generated"].values())


# ------------------------------------------------------------------ output
SIGN = {"create": "+", "delete": "-", "update": "~"}
MAX_LINES = 60  # per list in a comment: a big import shouldn't make an unreadable one


def _short(v) -> str:
    s = "∅" if v is None else str(v)
    return s if len(s) <= 60 else s[:57] + "…"


def text(p: dict) -> str:
    lines = []
    where = f"site {p['site']}" if p["site"] else "the inventory"
    if not p["ok"]:
        lines.append(f"The file can't be imported into {where}:")
        lines += [f"  {e.get('file', '')}: {e.get('field', '')}: {e.get('message', '')}" for e in p["errors"]]
        return "\n".join(lines)
    if empty(p):
        return f"No changes to {where}."
    s = p["summary"]
    lines.append(f"Plan for {where}: {s['create']} to add, {s['update']} to change, {s['delete']} to remove.")
    for c in p["inventory"]:
        lines.append(f"  {SIGN[c['op']]} {c['kind']} {c['name']}")
        for f in c["fields"]:
            lines.append(f"      {f['path']}: {_short(f['before'])} → {_short(f['after'])}")
    g = p["generated"]
    for label, key in (("DNS records", "dns"), ("DHCP reservations", "dhcp")):
        if g[key]["add"] or g[key]["remove"]:
            lines.append(f"{label}:")
            lines += [f"  + {r}" for r in g[key]["add"]] + [f"  - {r}" for r in g[key]["remove"]]
    if g["aliases"]:
        lines.append("Firewall aliases:")
        for a in g["aliases"]:
            detail = ", ".join([f"+{x}" for x in a["add"]] + [f"-{x}" for x in a["remove"]])
            lines.append(f"  {SIGN[a['op']]} {a['name']}" + (f": {detail}" if detail and a["op"] == "update" else ""))
    return "\n".join(lines)


def markdown(p: dict, title: str = "labdex plan") -> str:
    where = f"site `{p['site']}`" if p["site"] else "the inventory"
    out = [f"### {title}: {where}", ""]
    if not p["ok"]:
        out += ["❌ The file can't be imported:", "", "| File | Field | Problem |", "| --- | --- | --- |"]
        out += [f"| {e.get('file', '')} | `{e.get('field', '')}` | {_md(e.get('message', ''))} |" for e in p["errors"]]
        return "\n".join(out) + "\n"
    if empty(p):
        return "\n".join(out + ["No changes."]) + "\n"
    s = p["summary"]
    out.append(f"**{s['create']}** to add, **{s['update']}** to change, **{s['delete']}** to remove; "
               f"{s['dns']} DNS, {s['aliases']} alias and {s['dhcp']} DHCP changes.")
    if p["inventory"]:
        out += ["", "| | Kind | Name | What changes |", "| --- | --- | --- | --- |"]
        for c in p["inventory"][:MAX_LINES]:
            what = "<br>".join(f"`{f['path']}`: {_md(_short(f['before']))} → {_md(_short(f['after']))}" for f in c["fields"][:8])
            if len(c["fields"]) > 8:
                what += f"<br>… {len(c['fields']) - 8} more"
            out.append(f"| {SIGN[c['op']]} | {c['kind']} | `{c['name']}` | {what} |")
        if len(p["inventory"]) > MAX_LINES:
            out.append(f"\n… and {len(p['inventory']) - MAX_LINES} more objects.")
    g = p["generated"]
    for label, key in (("DNS records", "dns"), ("DHCP reservations", "dhcp")):
        if g[key]["add"] or g[key]["remove"]:
            rows = [f"+ {r}" for r in g[key]["add"]] + [f"- {r}" for r in g[key]["remove"]]
            out += ["", f"<details><summary>{label} ({len(rows)})</summary>", "", "```diff", *rows[:MAX_LINES], "```", "</details>"]
    if g["aliases"]:
        rows = []
        for a in g["aliases"][:MAX_LINES]:
            rows.append(f"{SIGN[a['op']] if a['op'] != 'update' else '!'} {a['name']}")
            if a["op"] == "update":
                rows += [f"+   {x}" for x in a["add"]] + [f"-   {x}" for x in a["remove"]]
        out += ["", f"<details><summary>Firewall aliases ({len(g['aliases'])})</summary>", "", "```diff", *rows, "```", "</details>"]
    return "\n".join(out) + "\n"


def _md(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")
