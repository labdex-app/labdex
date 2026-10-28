"""Whole-inventory YAML export and import.

The YAML file is the git-friendly form of the database: export it, commit it
(signed), review it like any other change. Import replaces the database
content with the file, in one transaction.
"""
from __future__ import annotations


import yaml

from . import features, hostnames, naming, racks, store
from .models import AliasGroup, Area, Feature, Host, Interface, Link, NamingPart, Network, Rack, RackItem, Service, Setting, Tag, Zone, db

INSTANCE_SETTINGS = ("official_repo_seeded", "bundled_installed", "plugin_holds", "plugin_update_mode", "plugin_updates_seen", "plugin_updates_checked", "digest_last")  # settings of this labdex, kept through an import

HEADER = "# labdex inventory. Edit here or in the web UI; import with `labdex import`.\n"


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_presenter)


def _clean(d: dict) -> dict:
    """Drop empty values so the file stays short and diffs stay readable."""
    return {k: v for k, v in d.items() if v not in ("", None, [], False) or k in ("name",)}


def export_dict() -> dict:
    from . import funpools
    from .models import FunPool
    nets = db.session.scalars(db.select(Network).order_by(Network.vlan.is_(None), Network.vlan, Network.name)).all()
    hosts = db.session.scalars(db.select(Host).order_by(Host.name)).all()
    groups = db.session.scalars(db.select(AliasGroup).order_by(AliasGroup.name)).all()
    out_hosts = []
    for h in hosts:
        d = store.host_to_dict(h)
        d["interfaces"] = [_clean({k: v for k, v in i.items() if k not in ("zone", "ip6_all")}) | ({"in_dns": i["in_dns"]} if i["in_dns"] is not None else {})
                           for i in d["interfaces"]]
        d.pop("vips", None)  # derived from the VIPs' member lists
        # each cable once, at the end that sorts first
        d["cables"] = [_clean(lk) for lk in d["cables"] if (h.name, lk["port"]) <= (lk["peer"], lk["peer_port"])]
        if not d["cables"]:
            d.pop("cables")
        if d.get("kind") == "device":
            d.pop("kind")
        d["services"] = [_clean(s) for s in d["services"]]
        d["records"] = [_clean(r) | {"type": r["type"]} for r in d["records"]]
        if not d["vars"]:
            d.pop("vars")
        names = d.pop("fun_names")
        if len(names) > 1:
            d.pop("fun", None)
            d["fun_names"] = names
        if d["status"] == "active":
            d.pop("status")
        if not d["rack"]:  # not in a rack: none of the rack fields
            for k in ("rack", "rack_unit", "rack_height", "rack_face"):
                d.pop(k)
        elif d["rack_face"] == "front":
            d.pop("rack_face")
        out_hosts.append(_clean(d))
    meta = store.meta()
    out_nets = []
    for n in nets:
        d = store.network_to_dict(n)
        in_dns = d.pop("in_dns")
        prefixes = d.pop("prefixes")
        fams = [p["family"] for p in prefixes]
        simple = (fams.count(4) <= 1 and fams.count(6) <= 1 and all(p["source"] == "static" and not p["description"] for p in prefixes)
                  and all(not (p["gateway"] or p["dhcp_start"]) for p in prefixes if p["family"] == 6))
        if not simple:  # several prefixes per family, delegated ones, descriptions: the full list
            for k in ("prefix4", "prefix6", "gateway4", "dhcp_start", "dhcp_end"):
                d.pop(k, None)
            d["prefixes"] = [_clean({k: v for k, v in p.items() if k != "family"}) | {"family": p["family"]} for p in prefixes]
        d = _clean(d)
        if not in_dns:
            d["in_dns"] = False
        out_nets.append(d)
    return {
        "settings": {k: v for k, v in meta["settings"].items() if k not in (funpools.SEEDED, "official_repo_seeded")},
        "areas": meta["areas"],
        "zones": [_clean({"code": z.code, "name": z.name, "rank": z.rank, "colour": z.colour, "description": z.description})
                  for z in (db.session.get(Zone, c) for c in naming.all_zones()) if z.code != z.area or z.colour or z.description
                  or (z.name and z.name != db.session.get(Area, z.area).name)],
        "naming": {{"category": "categories", "type": "types", "role": "roles"}[k]:
                   [_clean({"code": p["code"], "label": p["label"], "description": p["description"]}) for p in v]
                   for k, v in hostnames.parts().items()},
        **({"tags": tags} if (tags := [_clean({"name": t.name, "colour": t.colour, "description": t.description})
                                       for t in db.session.scalars(db.select(Tag).order_by(Tag.name)) if t.colour or t.description]) else {}),
        "fun_pools": [funpools.export_dict(p) for p in db.session.scalars(db.select(FunPool).order_by(FunPool.name))],
        **({"racks": rk} if (rk := [_rack_export(r) for r in db.session.scalars(db.select(Rack).order_by(Rack.site, Rack.position, Rack.name))]) else {}),
        "networks": out_nets,
        "features": [_clean(features.to_dict(f)) | {"path": f.path} for f in db.session.scalars(db.select(Feature).order_by(Feature.path))],
        "hosts": out_hosts,
        "aliases": [_clean(store.alias_to_dict(a)) for a in groups],
        **_policy_export(),
    }


def _rack_export(r) -> dict:
    d = racks.to_dict(r, with_hosts=True)
    items = [_clean({k: v for k, v in i.items() if k != "id"} | ({"face": i["face"]} if i["face"] != "front" else {}))
             for i in d["items"]]
    for i in items:
        if i.get("face") == "front":
            i.pop("face")
    return _clean({k: v for k, v in d.items() if k not in ("uid", "position", "hosts", "used", "items")} | {"items": items})


def _policy_export() -> dict:
    from . import policy
    cells = [_clean(policy.cell_dict(c)) for c in policy.cells()]
    return {"zone_policy": cells} if cells else {}


def export_yaml() -> str:
    return HEADER + yaml.dump(export_dict(), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


def _used_zones(data: dict) -> set[str]:
    used = {str(x.get("zone") or "").upper() for key in ("networks", "hosts") for x in data.get(key) or [] if isinstance(x, dict)}
    for c in data.get("zone_policy") or []:
        used |= {str(c.get("src", "")).upper(), str(c.get("dst", "")).upper()}
    for a in data.get("aliases") or []:
        used |= {str(m)[5:].upper() for m in a.get("members") or [] if str(m).startswith("zone:")}
    return used - {""}


def _import_zones(data: dict, errors: list) -> None:
    """Areas get their default zones; `zones:` adds the rest. A file from before 0.10.1 has
    `trust_levels:` instead: the area × trust combinations it uses become zones."""
    from . import zones as zmod
    for a in db.session.scalars(db.select(Area)):
        zmod.ensure_default(a)
    rows = data.get("zones")
    if rows is None and data.get("trust_levels"):
        rows = zmod.from_legacy(data.get("areas") or [], data["trust_levels"], _used_zones(data))
    for i, z in enumerate(rows or []):
        code = str(z.get("code", "")).upper()
        existing = db.session.get(Zone, code)
        try:
            zmod.save_zone({**z, "code": code}, existing if existing is not None and existing.code == existing.area else None)
        except store.ValidationError as exc:
            errors += [{**e, "field": f"zones[{i}] {code}.{e['field']}"} for e in exc.errors]


def _stable_ids() -> dict:
    """Before an import replaces everything: the ids of what has one (its URL) and which host each
    vault entry belongs to, by name, so the same objects keep them afterwards."""
    from .models import VaultEntry
    return {"host": {h.name: h.uid for h in db.session.scalars(db.select(Host))},
            "network": {n.name: n.uid for n in db.session.scalars(db.select(Network))},
            "zone": {z.code: z.uid for z in db.session.scalars(db.select(Zone))},
            "rack": {r.name: r.uid for r in db.session.scalars(db.select(Rack))},
            "vault": {e.id: e.host.name for e in db.session.scalars(db.select(VaultEntry)) if e.host is not None}}


def _restore_ids(ids: dict) -> None:
    from .models import VaultEntry
    for model, key, attr in ((Host, "host", "name"), (Network, "network", "name"), (Zone, "zone", "code"), (Rack, "rack", "name")):
        rows = list(db.session.scalars(db.select(model)))
        for r in rows:  # out of the way first, so swapping two ids can't collide
            if getattr(r, attr) in ids[key] and r.uid != ids[key][getattr(r, attr)]:
                r.uid = "~" + r.uid[:11]
        db.session.flush()
        for r in rows:
            if getattr(r, attr) in ids[key]:
                r.uid = ids[key][getattr(r, attr)]
        db.session.flush()
    for entry_id, host_name in ids["vault"].items():
        e = db.session.get(VaultEntry, entry_id)
        h = db.session.scalar(db.select(Host).where(Host.name == host_name))
        if e is not None and e.host_id is None and h is not None:
            e.host_id = h.id
    db.session.flush()


def import_dict(data: dict, commit: bool = True) -> dict:
    """Replace everything with `data`. Raises store.ValidationError (nothing is changed then)."""
    if not isinstance(data, dict):
        raise store.ValidationError([{"field": "file", "message": "expected a YAML mapping at the top level"}])
    errors = []
    try:
        # areas/trust levels are only replaced when the file has them
        from .models import FunPool, HostRecord, vip_member_table
        # settings of this labdex, not of the inventory: an import never changes them
        kept = {r.key: r.value for r in db.session.scalars(db.select(Setting).where(Setting.key.in_(INSTANCE_SETTINGS)))}
        ids = _stable_ids()
        db.session.expunge_all()  # bulk deletes below; nothing stale may stay in the session
        db.session.execute(vip_member_table.delete())
        from .models import NetworkPrefix
        models = [Link, HostRecord, Service, Interface, Host, AliasGroup, NetworkPrefix, Network, Feature, Setting]
        from .models import ZonePolicy
        models += [m for m, key in ((RackItem, "racks"), (Rack, "racks"), (Zone, "areas"), (Area, "areas"), (Tag, "tags"), (NamingPart, "naming"), (FunPool, "fun_pools"),
                                    (ZonePolicy, "zone_policy")) if key in data]
        for model in models:
            db.session.execute(db.delete(model))
        db.session.flush()

        for i, a in enumerate(data.get("areas") or []):
            db.session.add(Area(code=str(a["code"]).upper(), name=a.get("name", a["code"]), position=i,
                                description=a.get("description", ""), colour=str(a.get("colour") or "")))
        db.session.flush()
        if "areas" in data:
            _import_zones(data, errors)
        for i, t in enumerate(data.get("tags") or []):
            n = str(t.get("name", "")).strip().lower()
            if not store.TAG_RE.match(n):
                errors.append({"field": f"tags[{i}].name", "message": "lowercase letters, digits and - _ . :"})
                continue
            db.session.add(Tag(name=n, colour=str(t.get("colour") or ""), description=str(t.get("description") or "")))
        db.session.flush()
        naming.ensure_defaults_in_session()
        store.save_settings(data.get("settings") or {})
        for key, kind in (("categories", "category"), ("types", "type"), ("roles", "role")):
            for i, p in enumerate((data.get("naming") or {}).get(key) or []):
                p = p if isinstance(p, dict) else {"code": p}
                db.session.add(NamingPart(kind=kind, code=str(p["code"]).lower(), label=p.get("label", p["code"]),
                                          description=p.get("description", ""), position=i))
        db.session.flush()
        from . import funpools
        for i, fp in enumerate(data.get("fun_pools") or []):
            try:
                funpools.import_one(fp)
                db.session.flush()
            except store.ValidationError as exc:
                errors += [{**e, "field": f"fun_pools[{i}] {fp.get('name', '')}.{e['field']}"} for e in exc.errors]
        store.save_settings({funpools.SEEDED: "true"})  # pools are kept or replaced, never re-seeded by an import
        for k, v in kept.items():
            db.session.merge(Setting(key=k, value=v))

        for i, r in enumerate(data.get("racks") or []):
            try:
                racks.save(r)
            except store.ValidationError as exc:
                errors += [{**e, "field": f"racks[{i}] {r.get('name', '')}.{e['field']}"} for e in exc.errors]
        for i, n in enumerate(data.get("networks") or []):
            try:
                store.save_network(n)
                db.session.flush()
            except store.ValidationError as exc:
                errors += [{**e, "field": f"networks[{i}] {n.get('name', '')}.{e['field']}"} for e in exc.errors]

        for i, f in enumerate(sorted(data.get("features") or [], key=lambda f: str(f.get("path", "")))):
            try:
                features.save(f)
                db.session.flush()
            except store.ValidationError as exc:
                errors += [{**e, "field": f"features[{i}] {f.get('path', '')}.{e['field']}"} for e in exc.errors]
        if not errors:
            for f in data.get("features") or []:
                features.ensure_parents(str(f.get("path", "")).strip().lower())

        # hosts in two passes: create all, then set runs_on (it may point forward)
        hosts = data.get("hosts") or []
        for i, h in enumerate(hosts):
            try:
                store.save_host({**{k: v for k, v in h.items() if k != "cables"}, "runs_on": "", "vip_members": []})
                db.session.flush()
            except store.ValidationError as exc:
                errors += [{**e, "field": f"hosts[{i}] {h.get('name', '')}.{e['field']}"} for e in exc.errors]
        for i, h in enumerate(hosts):
            if h.get("runs_on"):
                host = db.session.scalar(db.select(Host).where(Host.name == str(h.get("name", "")).lower()))
                parent = db.session.scalar(db.select(Host).where(Host.name == h["runs_on"]))
                if host is not None and parent is None:
                    errors.append({"field": f"hosts[{i}] {h['name']}.runs_on", "message": f"unknown host '{h['runs_on']}'"})
                elif host is not None:
                    host.runs_on = parent
        for i, h in enumerate(hosts):  # cables, once every host exists
            if not h.get("cables"):
                continue
            host = store.find_host(str(h.get("name", "")))
            if host is None:
                continue
            e = store._Errors()
            for lk in store._links(h, e):
                peer = host if lk["peer"] is store.SELF else lk["peer"]  # a cable inside one host
                if peer is not None:
                    db.session.add(Link(a_host_id=host.id, a_port=lk["port"], b_host_id=peer.id, b_port=lk["peer_port"],
                                        medium=lk["medium"], speed=lk["speed"], notes=lk["notes"],
                                        label=lk["label"], length=lk["length"], colour=lk["colour"]))
            errors += [{**x, "field": f"hosts[{i}] {h['name']}.{x['field']}"} for x in e.items]
        for i, h in enumerate(hosts):  # VIP members may point forward too
            if h.get("vip_members"):
                vip = db.session.scalar(db.select(Host).where(Host.name == str(h.get("name", "")).lower()))
                if vip is None:
                    continue
                for m in h["vip_members"]:
                    mh = store.find_host(str(m))
                    if mh is None or mh.is_vip or mh is vip:
                        errors.append({"field": f"hosts[{i}] {h['name']}.vip_members", "message": f"'{m}' isn't a host that can hold a VIP"})
                    elif mh not in vip.vip_members:
                        vip.vip_members.append(mh)

        for i, a in enumerate(data.get("aliases") or []):
            try:
                store.save_alias(a)
                db.session.flush()
            except store.ValidationError as exc:
                errors += [{**e, "field": f"aliases[{i}] {a.get('name', '')}.{e['field']}"} for e in exc.errors]

        from . import policy
        for i, c in enumerate(data.get("zone_policy") or []):
            src, dst, ports = str(c.get("src", "")).upper(), str(c.get("dst", "")).upper(), list(c.get("ports") or [])
            errs = policy.validate(src, dst, ports)
            if errs:
                errors += [{**e, "field": f"zone_policy[{i}] {src}→{dst}.{e['field']}"} for e in errs]
            else:
                policy.put(src, dst, ports, str(c.get("description") or ""))
        db.session.flush()
        _restore_ids(ids)
    except (KeyError, TypeError, ValueError) as exc:
        db.session.rollback()
        raise store.ValidationError([{"field": "file", "message": f"malformed inventory: {exc!r}"}]) from exc

    from .cache import bump
    bump()
    if errors:
        db.session.rollback()
        raise store.ValidationError(errors)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return counts()


def import_yaml(text: str, commit: bool = True) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise store.ValidationError([{"field": "file", "message": f"not valid YAML: {exc}"}]) from exc
    return import_dict(data or {}, commit=commit)


def counts() -> dict:
    return {
        "networks": db.session.scalar(db.select(db.func.count()).select_from(Network)),
        "hosts": db.session.scalar(db.select(db.func.count()).select_from(Host)),
        "aliases": db.session.scalar(db.select(db.func.count()).select_from(AliasGroup)),
    }
