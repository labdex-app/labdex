"""One search over the inventory, for the command palette (Ctrl/⌘ K): hosts by name, fun
name, function, tag, address or MAC; networks by name, VLAN or an address inside them;
aliases; services; zones (everyone sees those) by code or name; integrations and the actions you may run. Only what you may see."""
from __future__ import annotations

import ipaddress
import re

from flask import current_app, g, jsonify, request

from .. import rbac, store
from ..generators import aliases as galiases
from ..models import Integration, Network, Zone, db
from . import bp

PER_KIND = 8
HEX = re.compile(r"[^0-9a-f]")


def _mac(q: str) -> str:
    """aa-bb-cc…, aabb.cc…, AABBCC… → aa:bb:cc:… (if it looks like a whole or partial MAC)."""
    h = HEX.sub("", q.lower())
    if len(h) >= 4 and len(h) % 2 == 0 and re.fullmatch(r"[0-9a-f:.\- ]+", q.lower()) and not re.fullmatch(r"[\d.]+", q):
        return ":".join(h[i:i + 2] for i in range(0, len(h), 2))
    return ""


def _ip(q: str):
    try:
        return ipaddress.ip_address(q)
    except ValueError:
        return None


def _score(q: str, *values) -> int | None:
    """0 = exact, 1 = starts with, 2 = contains; None = no match."""
    best = None
    for v in values:
        v = (v or "").lower()
        if not v:
            continue
        s = 0 if v == q else 1 if v.startswith(q) else 2 if q in v else None
        if s is not None and (best is None or s < best):
            best = s
    return best


def plan_actions(host: dict, p) -> list[dict]:
    """The plan actions that can run for this one host: host actions, and actions that take a
    host list, of enabled integrations that apply to it and that you may run. Each comes back as
    what to post: {integration, plugin, action, label, host | params}. Plans only: they wait on
    Changes for review and approval."""
    from .. import hostmodules
    reg = current_app.extensions["labdex"]["registry"]
    out = []
    for i in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
        spec = reg.get(i.plugin_id)
        if spec is None or spec.error or not p.can("integration", "apply", rbac.named_facts(i.name)):
            continue
        if not hostmodules.state(i, host)[0]:
            continue
        for a in spec.actions:
            if a.get("mode") != "plan":
                continue
            per_host = a.get("scope") == "host"
            takes_list = any(x.get("key") == "hosts" and x.get("type") == "list" for x in a.get("params") or [])
            if not (per_host or takes_list):
                continue
            r = {"integration": i.name, "plugin": i.plugin_id, "action": a["id"], "label": a.get("label") or a["id"]}
            out.append(r | ({"host": host["name"]} if per_host else {"params": {"hosts": [host["name"]]}}))
    return out


def _host_actions(q: str, p) -> list[dict]:
    """"patch web-02", "deploy motd to web*": a verb, then a host (a trailing * matches a prefix).
    Offers each host action, and each action that takes a host list, for the hosts that match,
    as plans to review on Changes. Nothing runs from here without the Changes page's approval."""
    words = q.split()
    if len(words) < 2:
        return []
    verb, target = words[0], words[-1]
    prefix = target.endswith("*")
    target = target.rstrip("*")
    if len(target) < 2:
        return []
    hosts = []
    for e in store.host_index():
        h = e["host"]
        if not p.can("host", "view", e["facts"]):
            continue
        names = [h["name"], *h.get("fun_names", [])]
        s = _score(target, *names)
        if s is not None and (not prefix or any(n.startswith(target) for n in names)):
            hosts.append((s, h))
    if not hosts:
        return []
    hosts = [h for _, h in sorted(hosts, key=lambda x: (x[0], x[1]["name"]))]
    from .. import hostmodules, patches
    reg = current_app.extensions["labdex"]["registry"]
    out = []
    for i in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
        spec = reg.get(i.plugin_id)
        if spec is None or spec.error or not p.can("integration", "apply", rbac.named_facts(i.name)):
            continue
        for a in spec.actions:
            per_host = a.get("scope") == "host"
            takes_list = any(x.get("key") == "hosts" and x.get("type") == "list" for x in a.get("params") or [])
            if not (per_host or takes_list) or a.get("mode") == "export":
                continue
            if _score(verb, a.get("label") or "", a["id"], *(w for w in (a.get("label") or "").lower().split())) is None:
                continue
            on = [h for h in hosts if hostmodules.state(i, h)[0]]
            label = a.get("label") or a["id"]
            verb_t = ("Plan " + label[0].lower() + label[1:]) if a.get("mode") == "plan" else label
            for h in on[:5]:
                sub = i.name
                if i.plugin_id == "patching":
                    st = patches.summary(h["name"])
                    bits = [f"{st['security']} security update{'s' if st['security'] != 1 else ''}" if st["security"] else
                            f"{st['pending']} update{'s' if st['pending'] != 1 else ''}" if st["pending"] else
                            "no updates reported" if st["state"] != "unknown" else "patch state not read yet",
                            "reboot needed" if st["reboot"] else ""]
                    sub = " · ".join(b for b in [*bits, i.name] if b)
                r = {"kind": "action", "title": f"{verb_t} on {h['name']}", "sub": sub, "integration": i.name, "action": a["id"],
                     "mode": a.get("mode"), "danger": bool(a.get("danger")), "target": h["name"]}
                r |= {"host": h["name"]} if per_host else {"params": {"hosts": [h["name"]]}}
                out.append(r)
            if takes_list and len(on) > 1:
                out.append({"kind": "action", "title": f"{verb_t} on all {len(on)} hosts matching {target}{'*' if prefix else ''}",
                            "sub": f"{', '.join(h['name'] for h in on[:4])}{' …' if len(on) > 4 else ''} · {i.name}",
                            "integration": i.name, "action": a["id"], "mode": a.get("mode"), "danger": bool(a.get("danger")),
                            "params": {"hosts": [h["name"] for h in on]}})
    return out[:PER_KIND]


@bp.get("/search")
def search():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify({"q": q, "results": []})
    p = g.principal
    mac, ip = _mac(q), _ip(q)
    out: list[tuple[int, int, dict]] = []  # (score, kind order, result)
    # "verb host": actions on those hosts come before everything else
    out += [(-2, -2, r) for r in _host_actions(q, p)]

    # tags: "tag:backup", or a word that is a tag, offers the host list filtered to it
    from ..models import Tag
    want_tag = q.removeprefix("tag:").strip() if q.startswith("tag:") else q
    for t in db.session.scalars(db.select(Tag).order_by(Tag.name)):
        s = _score(want_tag, t.name)
        if s is None or (not q.startswith("tag:") and s > 0):
            continue
        n = sum(1 for e in store.host_index() if t.name in (e["host"].get("tags") or []) and p.can("host", "view", e["facts"]))
        out.append((s, -1, {"kind": "filter", "title": f"Hosts tagged {t.name}", "sub": f"{n} host{'s' if n != 1 else ''}",
                            "link": f"/hosts?q=tag:{t.name}"}))
    if q.startswith("tag:"):
        out.sort(key=lambda x: (x[0], x[1]))
        return jsonify({"q": q, "results": [r for _, _, r in out[:30]]})

    # hosts
    hits = []
    for e in store.host_index():
        h = e["host"]
        if not p.can("host", "view", e["facts"]):
            continue
        addrs = [a for i in h["interfaces"] for a in (i.get("ip4"), i.get("ip6"), *(i.get("ip6_all") or []), *(i.get("extra_ips") or [])) if a]
        macs = [i.get("mac") or "" for i in h["interfaces"]]
        via = ""
        s = _score(q, h["name"], *h.get("fun_names", []))
        if s is None and (ip or "." in q or ":" in q):
            s = _score(q, *addrs)
            via = next((a for a in addrs if q in a.lower()), "")
        if s is None and mac:
            s = _score(mac, *macs)
            via = next((m for m in macs if mac in m.lower()), "")
        if s is None:
            s2 = _score(q, h.get("function") or "", *(h.get("tags") or []))
            s = None if s2 is None else s2 + 1
        if s is None:
            continue
        first = next((a for a in addrs if ":" not in a), addrs[0] if addrs else "")
        sub = " · ".join(x for x in (via or first, h.get("function") or "", h["zone"]) if x)
        hits.append((s, {"kind": "host", "title": h["name"], "fun": (h.get("fun_names") or [""])[0], "sub": sub,
                         "status": h["status"], "link": f"/hosts/{h.get('uid') or h['name']}"}))
    out += [(s, 0, r) for s, r in sorted(hits, key=lambda x: (x[0], x[1]["title"]))[:PER_KIND]]

    # networks: by name, VLAN, or the address you typed falls inside
    hits = []
    for n in db.session.scalars(db.select(Network)):
        d = store.network_to_dict(n)
        if not p.can("network", "view", rbac.network_facts(d)):
            continue
        s = _score(q, n.name)
        why = ""
        if s is None and n.vlan is not None and q.removeprefix("vlan").strip() == str(n.vlan):
            s, why = 0, f"VLAN {n.vlan}"
        if s is None and ip is not None:
            for cidr in [n.prefix4, n.prefix6, *[pp["cidr"] for pp in d.get("prefixes") or [] if pp.get("cidr")]]:
                try:
                    if cidr and ip in ipaddress.ip_network(cidr, strict=False):
                        s, why = 1, f"{ip} is in {cidr}"
                        break
                except ValueError:
                    continue
        if s is None:
            s = _score(q, n.prefix4 or "", n.prefix6 or "")
        if s is None:
            continue
        sub = " · ".join(x for x in (why or n.prefix4 or n.prefix6 or "", f"VLAN {n.vlan}" if n.vlan is not None and not why else "", n.zone) if x)
        hits.append((s, {"kind": "network", "title": n.name, "sub": sub, "link": f"/networks/{n.uid}"}))
    out += [(s, 1, r) for s, r in sorted(hits, key=lambda x: (x[0], x[1]["title"]))[:PER_KIND]]

    # zones: everyone who can sign in sees them, so no rights check (the sidebar's zone tree is gone; this replaces it)
    hits = []
    for z in db.session.scalars(db.select(Zone)):
        s = _score(q, z.code, z.name)
        if s is not None:
            hits.append((s, {"kind": "zone", "title": z.code, "sub": " · ".join(x for x in (z.name, f"area {z.area}") if x),
                             "link": f"/zones/{z.uid}"}))
    out += [(s, 1.5, r) for s, r in sorted(hits, key=lambda x: (x[0], x[1]["title"]))[:PER_KIND]]

    # services (on hosts you can see)
    hits = []
    for e in store.host_index():
        if not p.can("host", "view", e["facts"]):
            continue
        for sv in e["host"]["services"]:
            s = _score(q, sv["name"], sv.get("dns_name") or "", sv.get("public_name") or "")
            if s is None:
                continue
            port = f"{sv['port']}/{sv['proto']}" if sv.get("port") else ""
            hits.append((s, {"kind": "service", "title": sv["name"], "sub": " · ".join(x for x in (e["host"]["name"], port, sv.get("description") or "") if x),
                             "link": f"/hosts/{e['host'].get('uid') or e['host']['name']}?tab=services"}))
    out += [(s, 2, r) for s, r in sorted(hits, key=lambda x: (x[0], x[1]["title"]))[:PER_KIND]]

    # aliases
    hits = []
    fw = galiases.firewall_ids()
    for a in galiases.all_aliases():
        if not p.can("alias", "view", rbac.alias_facts(a.name)):
            continue
        fid = fw.get(a.name, a.name)
        s = _score(q, a.name, fid)
        if s is None:
            continue
        hits.append((s + 1, {"kind": "alias", "title": a.name, "sub": " · ".join(x for x in (fid if fid != a.name else "", a.kind, a.description) if x),
                             "link": f"/aliases?q={a.name}"}))
    out += [(s, 3, r) for s, r in sorted(hits, key=lambda x: (x[0], x[1]["title"]))[:PER_KIND]]

    # integrations and what you may run there
    reg = current_app.extensions["labdex"]["registry"]
    hits = []
    for i in db.session.scalars(db.select(Integration).order_by(Integration.name)):
        f = rbac.named_facts(i.name)
        if not p.can("integration", "view", f):
            continue
        spec = reg.get(i.plugin_id)
        label = spec.name if spec else i.plugin_id
        s = _score(q, i.name, label)
        if s is not None:
            hits.append((s + 1, {"kind": "integration", "title": i.name, "sub": label, "link": f"/integrations/{i.name}"}))
        if spec is None or spec.error or not i.enabled or not p.can("integration", "apply", f):
            continue
        for a in spec.actions:
            if a.get("scope") == "host" or a.get("mode") == "export" or any(x.get("required") for x in a.get("params") or []):
                continue
            s = _score(q, a.get("label") or a["id"], a["id"])
            if s is None and a.get("mode") == "plan" and q.lower() in ("plan", "plans"):
                s = 1  # "plan": every plan you may make (Plan changes on the Overview)
            if s is None and _score(q, i.name, label) is None:
                continue
            hits.append(((s if s is not None else 2) + 1, {"kind": "action", "title": a.get("label") or a["id"],
                                                          "sub": f"{i.name} · {'plan' if a.get('mode') == 'plan' else 'run'}",
                                                          "integration": i.name, "action": a["id"], "mode": a.get("mode"), "danger": bool(a.get("danger"))}))
    out += [(s, 4, r) for s, r in sorted(hits, key=lambda x: (x[0], x[1]["title"]))[:PER_KIND]]

    out.sort(key=lambda x: (x[0], x[1]))
    return jsonify({"q": q, "results": [r for _, _, r in out[:30]]})
