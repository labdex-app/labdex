"""Change sets: a batch of inventory changes you review as one unit, then apply together.

A change set is a patch in the shape of labdex's own YAML:

    networks: [{name: guest2, vlan: 232, zone: CA_UT, prefix4: 10.10.232.0/24}]   # added, or changed if it exists
    hosts:    [{name: cli-lt-ana-01, zone: CA_UT, interfaces: [{network: guest2, ip4: 10.10.232.5}]}]
    aliases:  [{name: MY_GROUP, type: host, members: [...]}]
    delete:   {hosts: [old-box], networks: [guest], aliases: [OLD_GROUP]}

For something that exists, the keys you give replace the same keys of the object; the others stay.
preview() applies the patch, compares the lint findings, the firewall aliases and the DNS records
before and after, and rolls everything back; apply() does the same and keeps it, one transaction and
one History entry per object, all naming the set. Nothing is saved when anything is invalid.
"""
from __future__ import annotations

import yaml

from . import audit, cache, lint, naming, rbac, store
from .generators import aliases as galiases
from .generators import dns
from .models import AliasGroup, Host, db

SECTIONS = ("networks", "hosts", "aliases", "delete")
DELETE_KEYS = ("hosts", "networks", "aliases")


class PatchError(ValueError):
    """The patch isn't a patch (wrong shape); errors holds {field, message}."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


def parse(text: str) -> dict:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PatchError([{"field": "patch", "message": f"not valid YAML: {exc}"}]) from exc
    return check(data)


def check(data) -> dict:
    errors = []
    if not isinstance(data, dict):
        raise PatchError([{"field": "patch", "message": "expected a mapping with networks, hosts, aliases and/or delete"}])
    for k in data:
        if k not in SECTIONS:
            errors.append({"field": k, "message": f"unknown section; use {', '.join(SECTIONS)}"})
    for k in ("networks", "hosts", "aliases"):
        for i, item in enumerate(data.get(k) or []):
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                errors.append({"field": f"{k}[{i}]", "message": "an object with a name"})
    d = data.get("delete") or {}
    if not isinstance(d, dict) or any(k not in DELETE_KEYS for k in d):
        errors.append({"field": "delete", "message": "a mapping of hosts, networks and/or aliases to lists of names"})
    else:
        for k, v in d.items():
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errors.append({"field": f"delete.{k}", "message": "a list of names"})
    if errors:
        raise PatchError(errors)
    return {k: data[k] for k in SECTIONS if data.get(k)}


def operations(patch: dict) -> list[dict]:
    """In the order they apply: networks and hosts and aliases are added or changed, then things are deleted
    (hosts first, so a network they sat in can go)."""
    ops = []
    for typ, key in (("network", "networks"), ("host", "hosts"), ("alias", "aliases")):
        for item in patch.get(key) or []:
            ops.append({"op": "upsert", "type": typ, "name": str(item["name"]).strip().lower() if typ != "alias" else str(item["name"]).strip(), "data": item})
    d = patch.get("delete") or {}
    for typ, key in (("host", "hosts"), ("alias", "aliases"), ("network", "networks")):
        for name in d.get(key) or []:
            ops.append({"op": "delete", "type": typ, "name": name if typ == "alias" else name.lower(), "data": None})
    return ops


# ------------------------------------------------------------------ before and after
def _snapshot() -> dict:
    cache.bump()
    domain = naming.get_setting("domain")
    return {
        "lint": {(i["code"], i["kind"], i["ref"], i["message"]): i for i in lint.run()},
        "aliases": {a.name: sorted(a.content) for a in galiases.all_aliases()},
        "dns": {(r.fqdn(domain), r.type, r.value) for r in dns.records()},
    }


def _compare(before: dict, after: dict) -> dict:
    a_new = sorted(set(after["lint"]) - set(before["lint"]))
    a_gone = sorted(set(before["lint"]) - set(after["lint"]))
    al_b, al_a = before["aliases"], after["aliases"]
    return {
        "lint": {"new": [after["lint"][k] for k in a_new], "resolved": [before["lint"][k] for k in a_gone]},
        "aliases": {"added": sorted(set(al_a) - set(al_b)), "removed": sorted(set(al_b) - set(al_a)),
                    "changed": sorted(n for n in set(al_a) & set(al_b) if al_a[n] != al_b[n])},
        "dns": {"added": sorted(f"{n} {t} {v}" for n, t, v in after["dns"] - before["dns"]),
                "removed": sorted(f"{n} {t} {v}" for n, t, v in before["dns"] - after["dns"])},
    }


# ------------------------------------------------------------------ running it
class _Denied(Exception):
    pass


def _allow(p, otype: str, facts) -> None:
    if p is not None and not p.can(otype, "edit", facts):
        raise _Denied(f"you may not edit {otype} '{facts.name}'")


def _apply_one(op: dict, p, label: str) -> dict:
    """Apply one operation inside the current transaction. Returns {action, fields} for the report."""
    from . import hostmodules
    typ, name, data = op["type"], op["name"], op["data"]
    if typ == "network":
        existing = store.find_network(name)
        if op["op"] == "delete":
            if existing is None:
                raise store.ValidationError([{"field": name, "message": f"no network '{name}'"}])
            before = store.network_to_dict(existing)
            _allow(p, "network", rbac.network_facts(before))
            db.session.flush()
            db.session.refresh(existing)  # hosts deleted earlier in this set no longer count
            if existing.interfaces:
                hosts = sorted({i.host.name for i in existing.interfaces})
                raise store.ValidationError([{"field": name, "message": f"{len(hosts)} host(s) still use it: {', '.join(hosts[:8])}"}])
            db.session.delete(existing)
            audit.record("delete", "network", name, before, None, summary=label)
            return {"action": "delete", "fields": []}
        before = store.network_to_dict(existing) if existing else None
        merged = {**(before or {}), **data}
        if existing is not None:
            _allow(p, "network", rbac.network_facts(before))
        _allow(p, "network", rbac.network_facts({"name": str(merged.get("name", "")).lower(), "zone": str(merged.get("zone", "")).upper()}))
        hosts_before = {h.id: store.host_to_dict(h) for h in db.session.scalars(db.select(Host).where(Host.zone_pinned.is_(False)))}
        n = store.save_network(merged, existing=existing)
        db.session.flush()
        after = store.network_to_dict(n)
        audit.record("update" if existing else "create", "network", n.name, before, after, summary=label)
        if existing is not None:
            for h in store.follow_network_zone(n):
                db.session.flush()
                audit.record("update", "host", h.name, hosts_before.get(h.id), store.host_to_dict(h), summary=f"{label}: zone follows {n.name}")
        return {"action": "update" if existing else "create", "fields": [d["path"] for d in audit.diff(before, after)] if existing else []}
    if typ == "host":
        existing = store.find_host(name)
        if op["op"] == "delete":
            if existing is None:
                raise store.ValidationError([{"field": name, "message": f"no host '{name}'"}])
            before = store.host_to_dict(existing)
            _allow(p, "host", rbac.host_facts(before))
            db.session.delete(existing)
            hostmodules.delete_host(before["name"])
            audit.record("delete", "host", name, before, None, summary=label)
            return {"action": "delete", "fields": []}
        before = store.host_to_dict(existing) if existing else None
        merged = {**(before or {}), **data}
        facts = rbac.host_facts({"name": str(merged.get("name", "")).lower(), "zone": str(merged.get("zone", "")).upper(), "fun": str(merged.get("fun") or "").lower(),
                                 "tags": merged.get("tags") or [], "interfaces": merged.get("interfaces") or []})
        if existing is not None:
            _allow(p, "host", rbac.host_facts(before))
        _allow(p, "host", facts)
        h = store.save_host(merged, existing=existing)
        db.session.flush()
        after = store.host_to_dict(h)
        if existing is not None and existing.name != before["name"]:
            hostmodules.rename_host(before["name"], h.name)
        audit.record("update" if existing else "create", "host", h.name, before, after, summary=label)
        return {"action": "update" if existing else "create", "fields": [d["path"] for d in audit.diff(before, after)] if existing else []}
    # alias
    existing = db.session.scalar(db.select(AliasGroup).where(AliasGroup.name == name))
    if op["op"] == "delete":
        if existing is None:
            raise store.ValidationError([{"field": name, "message": f"no alias '{name}'"}])
        before = store.alias_to_dict(existing)
        _allow(p, "alias", rbac.alias_facts(name))
        db.session.delete(existing)
        audit.record("delete", "alias", name, before, None, summary=label)
        return {"action": "delete", "fields": []}
    before = store.alias_to_dict(existing) if existing else None
    _allow(p, "alias", rbac.alias_facts(name))
    a = store.save_alias({**(before or {}), **data}, existing=existing)
    db.session.flush()
    after = store.alias_to_dict(a)
    audit.record("update" if existing else "create", "alias", a.name, before, after, summary=label)
    return {"action": "update" if existing else "create", "fields": [d["path"] for d in audit.diff(before, after)] if existing else []}


def run(patch: dict, name: str = "", principal=None, apply: bool = False) -> dict:
    """Apply the patch in one transaction. Without apply (or when anything failed) everything is rolled back.
    Returns {ok, applied, operations: [{op, type, name, action, fields, errors}], lint, aliases, dns}."""
    patch = check(patch)
    ops = operations(patch)
    label = f"change set “{name}”" if name else "change set"
    report: dict = {"ok": True, "applied": False, "operations": []}
    try:
        store.ensure_transaction()
        before = _snapshot()
        for op in ops:
            row = {"op": op["op"], "type": op["type"], "name": op["name"], "action": "", "fields": [], "errors": []}
            try:
                with db.session.begin_nested():
                    row.update(_apply_one(op, principal, label))
            except store.ValidationError as exc:
                row["errors"] = [f"{e['field']}: {e['message']}" if e["field"] != op["name"] else e["message"] for e in exc.errors]
            except _Denied as exc:
                row["errors"] = [str(exc)]
            report["operations"].append(row)
        report["ok"] = not any(r["errors"] for r in report["operations"])
        if report["ok"]:
            db.session.flush()
            db.session.expire_all()  # replaced interface rows must not linger in network.interfaces
            report |= _compare(before, _snapshot())
        if apply and report["ok"]:
            db.session.commit()
            report["applied"] = True
        else:
            db.session.rollback()
    except Exception:
        db.session.rollback()
        raise
    finally:
        cache.bump()
    return report
