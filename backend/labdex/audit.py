"""Change history: every create/update/delete, import, revert, plan apply and login."""
from __future__ import annotations

from flask import has_request_context, request

from . import rbac, store
from .models import AliasGroup, AuditEvent, Host, Network, db

INVENTORY_TYPES = ("host", "network", "alias", "setting", "inventory", "feature", "hostmodule", "area", "trust", "zone", "tag", "naming", "zone_policy", "rack", "secret")


def _actor() -> tuple[str, str]:
    p = rbac.current()
    if p.user is None:
        return "system", "system"
    return p.label, p.via or "user"


def _ip() -> str:
    from . import auth
    try:
        return auth.client_ip()
    except RuntimeError:
        return request.remote_addr or ""


def record(action: str, object_type: str, object_name: str = "", before=None, after=None,
           summary: str = "", actor: tuple[str, str] | None = None) -> AuditEvent:
    who, kind = actor or _actor()
    ev = AuditEvent(
        actor=who, actor_kind=kind, action=action, object_type=object_type, object_name=object_name,
        before=before, after=after, summary=summary or _summary(action, object_type, object_name, before, after),
        ip=_ip() if has_request_context() else "",
    )
    db.session.add(ev)
    db.session.flush()
    return ev


def _summary(action, object_type, name, before, after) -> str:
    if action == "update" and before and after:
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        return f"changed {', '.join(changed)}" if changed else "no field changes"
    return f"{action} {object_type} {name}".strip()


def inventory_revision() -> int:
    """Increases with every inventory change; plans remember it to detect staleness."""
    return db.session.scalar(
        db.select(db.func.max(AuditEvent.id)).where(AuditEvent.object_type.in_(INVENTORY_TYPES))
    ) or 0


def diff(before: dict | None, after: dict | None, prefix: str = "") -> list[dict]:
    """Flat list of changed paths."""
    out = []
    if isinstance(before, dict) or isinstance(after, dict):
        b, a = before or {}, after or {}
        for k in sorted(set(b) | set(a), key=str):
            out += diff(b.get(k), a.get(k), f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(before, list) and isinstance(after, list) and all(isinstance(x, dict) for x in before + after):
        for i in range(max(len(before), len(after))):
            out += diff(before[i] if i < len(before) else None, after[i] if i < len(after) else None, f"{prefix}[{i}]")
        return out
    if before != after:
        out.append({"path": prefix, "before": before, "after": after})
    return out


def to_dict(ev: AuditEvent, with_diff=False) -> dict:
    d = {
        "id": ev.id, "ts": ev.ts.isoformat() + "Z", "actor": ev.actor, "actor_kind": ev.actor_kind,
        "action": ev.action, "object_type": ev.object_type, "object_name": ev.object_name,
        "summary": ev.summary, "ip": ev.ip, "revertible": revertible(ev),
    }
    if with_diff:
        d["before"], d["after"] = ev.before, ev.after
        d["diff"] = diff(ev.before, ev.after)
    return d


def visible(ev: AuditEvent) -> bool:
    p = rbac.current()
    if p.is_admin:
        return True
    if ev.object_type in ("host", "network", "alias"):
        if not p.can("audit", "view", rbac.facts_for(ev.object_type, ev.after or ev.before, ev.object_name)):
            return False
        return p.can(ev.object_type, "view", rbac.facts_for(ev.object_type, ev.after or ev.before, ev.object_name))
    if ev.object_type == "integration":
        return p.can("integration", "view", rbac.named_facts(ev.object_name))
    if ev.object_type == "hostmodule":
        host_name, _, integ = ev.object_name.partition("/")
        h = db.session.scalar(db.select(Host).where(Host.name == host_name))
        facts = rbac.host_facts(store.host_to_dict(h)) if h else rbac.named_facts(host_name)
        return p.can("audit", "view", facts) and p.can("host", "view", facts) and p.can("integration", "view", rbac.named_facts(integ))
    if ev.object_type == "secret":  # path and field names, never values
        return p.can_any("audit", "view") and p.can("secret", "view", rbac.named_facts(ev.object_name))
    if ev.object_type in ("feature", "rack"):
        return p.can_any("audit", "view")
    return False  # users, settings, logins: admins only


# ---------------------------------------------------------------------- revert
_MODELS = {"host": (Host, store.save_host, store.host_to_dict),
           "network": (Network, store.save_network, store.network_to_dict),
           "alias": (AliasGroup, store.save_alias, store.alias_to_dict)}


def revertible(ev: AuditEvent) -> bool:
    return ev.object_type in _MODELS and ev.action in ("create", "update", "delete", "revert")


def revert(ev: AuditEvent) -> AuditEvent:
    """Put the object back the way it was before this event. Raises ValidationError."""
    model, save, to_dict_fn = _MODELS[ev.object_type]
    current_name = (ev.after or {}).get("name") or ev.object_name
    obj = db.session.scalar(db.select(model).where(model.name == current_name))
    before_now = to_dict_fn(obj) if obj is not None else None
    target = ev.before

    facts_old = rbac.facts_for(ev.object_type, before_now, current_name)
    facts_new = rbac.facts_for(ev.object_type, target, (target or {}).get("name", current_name))
    if obj is not None:
        rbac.require(ev.object_type, "edit", facts_old)
    if target is not None:
        rbac.require(ev.object_type, "edit", facts_new)

    if target is None:  # the event created it: delete
        if obj is None:
            raise store.ValidationError([{"field": "revert", "message": "already gone"}])
        db.session.delete(obj)
        after = None
    else:
        if ev.object_type == "host" and target.get("runs_on"):
            if db.session.scalar(db.select(Host).where(Host.name == target["runs_on"])) is None:
                target = {**target, "runs_on": ""}
        obj = save(target, existing=obj)
        db.session.flush()
        after = to_dict_fn(obj)
    return record("revert", ev.object_type, (after or before_now or {}).get("name", current_name),
                  before_now, after, summary=f"reverted event #{ev.id}")
