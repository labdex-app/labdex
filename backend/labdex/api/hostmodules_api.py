"""Integrations as seen from one host: on/off, host settings, host actions, host view."""
from __future__ import annotations

from flask import abort, current_app, g, jsonify, request

from .. import audit, hostmodules, rbac, store
from ..models import Host, HostModule, Integration, Job, db
from ..plugins import coerce_fields, jobs, public_fields, resolved_fields, resolved_settings
from ..plugins.base import Context
from . import body, bp, err
from .plugins_api import job_dict


def _reg():
    return current_app.extensions["labdex"]["registry"]


def _host(name: str, action="view") -> tuple[Host, dict]:
    h = store.find_host(name)
    d = store.host_to_dict(h) if h else None
    if h is None or not g.principal.can("host", "view", rbac.host_facts(d)):
        abort(404, description=f"no host '{name}'")
    rbac.require("host", action, rbac.host_facts(d))
    return h, d


def _integ(name: str, action="view") -> Integration:
    i = db.session.scalar(db.select(Integration).where(Integration.name == name))
    if i is None or not g.principal.can("integration", "view", rbac.named_facts(name)):
        abort(404, description=f"no integration '{name}'")
    rbac.require("integration", action, rbac.named_facts(name))
    return i


def _entry(integ: Integration, spec, host: dict, can_edit_host: bool) -> dict:
    hm = hostmodules.override(integ, host["name"])
    on, why = hostmodules.state(integ, host, hm)
    values, secrets_set, refs = public_fields(spec.host_settings, (hm.settings if hm else {}) or {})
    p = g.principal
    last = [job_dict(j) for j in db.session.scalars(
        db.select(Job).where(Job.integration_id == integ.id).order_by(Job.id.desc()).limit(40))
        if (j.params or {}).get("host") == host["name"]][:5]
    return {
        "integration": integ.name, "plugin_id": spec.id, "plugin_name": spec.name, "kind": spec.kind,
        "integration_enabled": integ.enabled, "enabled": on, "why": why,
        "override": hm.enabled if hm else None, "selectors": list(integ.selectors or []),
        "host_settings": spec.host_settings, "settings": values, "secrets_set": secrets_set, "secret_refs": refs,
        "actions": [a for a in spec.as_dict()["actions"] if a.get("scope") == "host"],
        "has_view": spec.host_view, "jobs": last,
        "can_edit": can_edit_host,
        "can_apply": p.can("integration", "apply", rbac.named_facts(integ.name)) and p.can("host", "apply", rbac.host_facts(host)),
    }


@bp.get("/hosts/<name>/modules")
def host_modules(name):
    h, d = _host(name)
    can_edit = g.principal.can("host", "edit", rbac.host_facts(d))
    out = []
    for integ in db.session.scalars(db.select(Integration).order_by(Integration.name)):
        spec = _reg().get(integ.plugin_id)
        if spec is None or spec.error or not spec.per_host:
            continue
        if not g.principal.can("integration", "view", rbac.named_facts(integ.name)):
            continue
        out.append(_entry(integ, spec, d, can_edit))
    return jsonify(out)


@bp.put("/hosts/<name>/modules/<integration>")
def set_host_module(name, integration):
    h, d = _host(name, "edit")
    integ = _integ(integration)
    spec = _reg().get(integ.plugin_id)
    if spec is None or spec.error or not spec.per_host:
        return err(409, "this plugin has nothing per host")
    data = body()
    hm = hostmodules.override(integ, h.name)
    before = {"enabled": hm.enabled, **public_fields(spec.host_settings, hm.settings or {})[0]} if hm else None
    if hm is None:
        hm = HostModule(integration_id=integ.id, host_name=h.name, enabled=None, settings={})
        db.session.add(hm)
    vault = current_app.extensions["labdex"]["vault"]
    if "settings" in data:
        stored, errors = coerce_fields(spec.host_settings, data["settings"] or {}, hm.settings or {}, vault)
        if errors:
            db.session.rollback()
            return err(422, "validation failed", errors)
        hm.settings = stored
    if "enabled" in data:
        if data["enabled"] not in (True, False, None):
            return err(422, "validation failed", [{"field": "enabled", "message": "true, false or null (follow the selectors)"}])
        hm.enabled = data["enabled"]
    after = {"enabled": hm.enabled, **public_fields(spec.host_settings, hm.settings or {})[0]}
    if hm.enabled is None and not hm.settings:
        db.session.delete(hm)  # nothing left to override
    db.session.flush()
    audit.record("update" if before else "create", "hostmodule", f"{h.name}/{integ.name}", before, after,
                 summary=f"{integ.name} on {h.name}: " + audit._summary("update", "", "", before or {}, after))
    db.session.commit()
    jobs.emit("hostmodule.updated")
    return jsonify(_entry(integ, spec, d, True))


@bp.get("/hosts/<name>/modules/<integration>/view")
def host_module_view(name, integration):
    h, d = _host(name)
    integ = _integ(integration)
    spec = _reg().get(integ.plugin_id)
    if spec is None or spec.error or not spec.host_view:
        return err(404, "this plugin has no host view")
    if not integ.enabled:
        return err(409, "the integration is disabled")
    vault = current_app.extensions["labdex"]["vault"]
    ctx = Context(integ.name, spec, resolved_settings(spec, integ.settings or {}, vault), lambda m: None, integration=integ)
    obj = spec.plugin_class()(ctx) if spec.plugin_class() else None
    if obj is None or not hasattr(obj, "host_view"):
        return err(404, "plugin has no host_view()")
    hm = hostmodules.override(integ, h.name)
    d["module_settings"] = resolved_fields(spec.host_settings, (hm.settings if hm else {}) or {}, vault)
    d["module_enabled"] = hostmodules.state(integ, d, hm)[0]
    return jsonify(obj.host_view(d))


@bp.post("/hosts/<name>/modules/<integration>/actions/<action>")
def host_module_action(name, integration, action):
    h, d = _host(name, "apply")
    integ = _integ(integration, "apply")
    if not integ.enabled:
        return err(409, "enable the integration first")
    spec = _reg().get(integ.plugin_id)
    a = spec.action(action) if spec else None
    if a is None or a.get("scope") != "host":
        return err(404, f"no host action '{action}'")
    params = {**((request.get_json(silent=True) or {}).get("params") or {}), "host": h.name}
    job = jobs.start(integ, action, params=params, created_by=g.principal.label)
    db.session.expire_all()
    return jsonify(job_dict(db.session.get(Job, job.id), full=True)), 202
