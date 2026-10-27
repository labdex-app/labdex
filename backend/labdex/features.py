"""The Ansible feature tree.

Features are dotted paths: `server`, `server.packages`, `server.packages.pve`.
Every node can carry variables (like group_vars) and a list of playbooks and
roles. A host that has `server.packages.pve` gets everything along the path:

    vars       = server.vars ⊕ server.packages.vars ⊕ server.packages.pve.vars ⊕ host.vars
    playbooks  = server's, then server.packages', then server.packages.pve's

`⊕` is a deep merge where the right side wins; lists are replaced, not
appended. A host with several features merges them in the order listed.
"""
from __future__ import annotations

import re

import yaml

from . import store
from .models import Feature, Host, db

PATH_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
REL_RE = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_./-]+$")


def chain(path: str) -> list[str]:
    """kube.cluster.oak → [kube, kube.cluster, kube.cluster.oak]"""
    parts = path.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def deep_merge(a: dict, b: dict) -> dict:
    """b over a: dicts merge key by key, lists add the items a doesn't have yet (a's first),
    anything else is replaced. `{$replace: value}` replaces even a dict or a list."""
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and set(v) == {"$replace"}:
            out[k] = v["$replace"]
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = list(out[k]) + [x for x in v if x not in out[k]]
        else:
            out[k] = _strip(v)
    return out


def _strip(v):
    """A {$replace: …} with nothing under it is just its value."""
    if isinstance(v, dict):
        if set(v) == {"$replace"}:
            return _strip(v["$replace"])
        return {k: _strip(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def all_features() -> dict[str, Feature]:
    return {f.path: f for f in db.session.scalars(db.select(Feature).order_by(Feature.path))}


def to_dict(f: Feature, with_usage=False) -> dict:
    d = {"path": f.path, "description": f.description or "", "vars": f.vars or {},
         "playbooks": list(f.playbooks or []), "roles": list(f.roles or [])}
    if with_usage:
        d["hosts"] = hosts_with(f.path)
        d["direct_hosts"] = [h.name for h in db.session.scalars(db.select(Host).order_by(Host.name)) if f.path in (h.features or [])]
    return d


def parse_vars(raw, field="vars") -> dict:
    """Accept a dict, or YAML/JSON text (the UI sends text)."""
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = yaml.safe_load(str(raw))
    except yaml.YAMLError as exc:
        raise store.ValidationError([{"field": field, "message": f"not valid YAML: {str(exc).splitlines()[0]}"}]) from exc
    if val is None:
        return {}
    if not isinstance(val, dict):
        raise store.ValidationError([{"field": field, "message": "must be a mapping (key: value)"}])
    return val


def save(data: dict, existing: Feature | None = None) -> Feature:
    errors = []
    path = str(data.get("path", "")).strip().lower()
    if not PATH_RE.match(path) or len(path) > 128:
        errors.append({"field": "path", "message": "dotted lowercase names, e.g. kube.cluster.oak"})
    clash = db.session.scalar(db.select(Feature).where(Feature.path == path))
    if clash is not None and clash is not existing:
        errors.append({"field": "path", "message": "already exists"})
    try:
        fvars = parse_vars(data.get("vars"))
    except store.ValidationError as exc:
        errors += exc.errors
        fvars = {}
    else:  # a secret pointed at for the first time: it must exist, and you may hand it out
        from . import ansiblevault, secretstore
        errors += secretstore.check_new(ansiblevault.refs(existing.vars or {}) if existing else set(), ansiblevault.refs(fvars), "vars", must_exist=False)
    lists = {}
    for key in ("playbooks", "roles"):
        raw = data.get(key) or []
        if isinstance(raw, str):
            raw = re.split(r"[\n,]+", raw)
        items = [str(x).strip() for x in raw if str(x).strip()]
        bad = [x for x in items if not REL_RE.match(x)]
        if bad:
            errors.append({"field": key, "message": f"relative paths/names only: {', '.join(bad)}"})
        lists[key] = items
    if errors:
        raise store.ValidationError(errors)
    f = existing or Feature()
    old_path = f.path if existing else None
    f.path, f.description, f.vars = path, str(data.get("description", "")), fvars
    f.playbooks, f.roles = lists["playbooks"], lists["roles"]
    if existing is None:
        db.session.add(f)
    if old_path and old_path != path:  # rename: follow on hosts and children
        for h in db.session.scalars(db.select(Host)):
            if h.features:
                h.features = [path + p[len(old_path):] if p == old_path or p.startswith(old_path + ".") else p for p in h.features]
        for child in db.session.scalars(db.select(Feature).where(Feature.path.like(old_path + ".%"))):
            child.path = path + child.path[len(old_path):]
    return f


def ensure_parents(path: str) -> list[Feature]:
    """Creates the missing features along a dotted path (a, a.b for a.b.c); returns the new ones."""
    made = []
    for p in chain(path)[:-1]:
        if db.session.scalar(db.select(Feature).where(Feature.path == p)) is None:
            f = Feature(path=p, description="", vars={}, playbooks=[], roles=[])
            db.session.add(f)
            made.append(f)
    if made:
        db.session.flush()
    return made


def delete(f: Feature) -> None:
    children = list(db.session.scalars(db.select(Feature).where(Feature.path.like(f.path + ".%"))))
    used = hosts_with(f.path)
    if used:
        raise store.ValidationError([{"field": "path", "message": f"still used by {', '.join(used[:8])}{'…' if len(used) > 8 else ''}"}])
    for c in children:
        db.session.delete(c)
    db.session.delete(f)


def hosts_with(path: str) -> list[str]:
    """Hosts that have this feature or one below it."""
    out = []
    for h in db.session.scalars(db.select(Host).order_by(Host.name)):
        if any(p == path or p.startswith(path + ".") for p in h.features or []):
            out.append(h.name)
    return out


def expanded(host_features: list[str]) -> list[str]:
    """Every feature path a host gets, ancestors first, no duplicates."""
    seen, out = set(), []
    for p in host_features or []:
        for c in chain(p):
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def host_vars(h: Host, feats: dict[str, Feature] | None = None) -> dict:
    feats = feats if feats is not None else all_features()
    merged: dict = {}
    for p in expanded(h.features or []):
        if p in feats:
            merged = deep_merge(merged, feats[p].vars or {})
    return deep_merge(merged, h.vars or {})


def check_host_features(paths: list[str]) -> list[str]:
    """Unknown feature paths (the leaf has to exist; parents are implied by it)."""
    feats = all_features()
    return [p for p in paths if p not in feats]
