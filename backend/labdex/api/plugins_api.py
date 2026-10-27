"""Plugins, integration instances, actions, jobs, exports and plugin endpoints."""
from __future__ import annotations

import re

from flask import current_app, g, jsonify, request

from .. import audit, rbac
from ..models import Integration, Job, db
from ..plugins import catalog, installer
from ..plugins import coerce_settings, jobs, public_fields, public_settings, resolved_settings
from ..plugins.base import Context, PluginError
from . import body, bp, err, text_response

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _reg():
    return current_app.extensions["labdex"]["registry"]


def _vault():
    return current_app.extensions["labdex"]["vault"]


def _facts(name: str) -> rbac.Facts:
    return rbac.named_facts(name)


def _instance(name: str, action="view") -> Integration:
    integ = db.session.scalar(db.select(Integration).where(Integration.name == name))
    if integ is None or not g.principal.can("integration", "view", _facts(name)):
        from flask import abort
        abort(404, description=f"no integration '{name}'")
    rbac.require("integration", action, _facts(name))
    return integ


def _template_name(tid):
    if not tid:
        return None
    from ..models import JobTemplate
    t = db.session.get(JobTemplate, tid)
    return t.name if t else None


def job_dict(j: Job, full=False) -> dict:
    d = {"id": j.id, "integration": j.integration.name if j.integration else None,
         "plugin": j.integration.plugin_id if j.integration else None, "action": j.action, "mode": j.mode,
         "status": j.status, "trigger": j.trigger, "created_by": j.created_by, "applied_by": j.applied_by,
         "created_at": j.created_at.isoformat() + "Z",
         "finished_at": j.finished_at.isoformat() + "Z" if j.finished_at else None,
         "changes": len(j.plan or []), "result": j.result, "worker": j.worker, "hosts": [h.host for h in j.hosts],
         "template": _template_name(j.template_id)}
    if j.mode == "plan":
        from .. import approvals
        d.update({"approvals": list(j.approvals or []), "needed": approvals.needed(j) if j.policy else None,
                  "policy": (j.policy or {}).get("name"),
                  "apply_after": j.apply_after.isoformat() + "Z" if j.apply_after else None})
    if full:
        d.update({"plan": j.plan or [], "log": j.log or "", "params": j.params or {},
                  "can_apply": g.principal.can("integration", "apply", _facts(d["integration"] or ""))})
        if j.mode == "plan":
            from .. import approvals
            why = approvals.why_not(j, g.principal) if d["can_apply"] else "you may not apply this integration's plans"
            d.update({"can_approve": not why, "why_not": why, "policy_detail": j.policy, "replaces": j.replaces,
                      "replanned_as": (j.result or {}).get("replanned_as")})
    return d


def instance_dict(integ: Integration, full=False) -> dict:
    spec = _reg().get(integ.plugin_id)
    p = g.principal
    d = {"id": integ.id, "name": integ.name, "plugin_id": integ.plugin_id, "enabled": integ.enabled,
         "plugin_name": spec.name if spec else integ.plugin_id, "kind": spec.kind if spec else "",
         "error": (spec.error if spec else "plugin not installed"),
         "can_apply": p.can("integration", "apply", _facts(integ.name)),
         "selectors": list(integ.selectors or []), "per_host": bool(spec and spec.per_host),
         "can_admin": p.can("integration", "admin", _facts(integ.name))}
    last = db.session.scalar(db.select(Job).where(Job.integration_id == integ.id).order_by(Job.id.desc()).limit(1))
    d["last_job"] = job_dict(last) if last else None
    if full and spec:
        d["plugin"] = spec.as_dict()
        d["settings"], d["secrets_set"], d["secret_refs"] = public_fields(spec.settings, integ.settings or {})
        from .. import hostmodules
        from ..models import HostModule
        d["host_overrides"] = [{"host": hm.host_name, "enabled": hm.enabled, "settings": bool(hm.settings)}
                               for hm in db.session.scalars(db.select(HostModule).where(HostModule.integration_id == integ.id).order_by(HostModule.host_name))]
        if spec.per_host:
            d["hosts"] = [h["name"] for h in hostmodules.enabled_hosts(integ, include_offline=True)]
        d["jobs"] = [job_dict(j) for j in db.session.scalars(
            db.select(Job).where(Job.integration_id == integ.id).order_by(Job.id.desc()).limit(25))]
        d["pending_plans"] = [job_dict(j) for j in db.session.scalars(
            db.select(Job).where(Job.integration_id == integ.id, Job.status == "planned").order_by(Job.id.desc()))]
    return d


# ---------------------------------------------------------------------- plugins
@bp.get("/plugins")
def list_plugins():
    if not g.principal.can_any("integration", "view"):
        return err(403, "you lack integration:view")
    reg = _reg()
    p = g.principal
    by_plugin: dict[str, list] = {}
    for i in db.session.scalars(db.select(Integration).order_by(Integration.name)):
        if p.can("integration", "view", _facts(i.name)):
            last = db.session.scalar(db.select(Job).where(Job.integration_id == i.id).order_by(Job.id.desc()).limit(1))
            by_plugin.setdefault(i.plugin_id, []).append({"name": i.name, "enabled": i.enabled,
                                                          "last_status": last.status if last else None})
    from ..plugins import CAPABILITIES, sandbox
    specs = sorted(reg.specs.values(), key=lambda s: s.name.lower())
    return jsonify({"plugins": [s.as_dict() | {"instances": by_plugin.get(s.id, []), "installed": True}
                                for s in specs if catalog.is_installed(s)],
                    "available": sum(1 for s in specs if not catalog.is_installed(s) and not s.error),
                    "missing": [{"id": pid, "instances": [x["name"] for x in by_plugin.get(pid, [])]} for pid in installer.missing()],
                    "errors": reg.errors, "capabilities": CAPABILITIES,
                    "isolation": sandbox.status(current_app.config.get("LABDEX_PLUGIN_SANDBOX", "on"))})


@bp.get("/plugins/<pid>")
def get_plugin(pid):
    """One plugin: what it brings, its instances (integrations) and their recent jobs."""
    if not g.principal.can_any("integration", "view"):
        return err(403, "you lack integration:view")
    spec = _reg().get(pid)
    if spec is None:
        return err(404, f"no plugin '{pid}'")
    p = g.principal
    insts = [i for i in db.session.scalars(db.select(Integration).where(Integration.plugin_id == pid).order_by(Integration.name))
             if p.can("integration", "view", _facts(i.name))]
    jobs_ = []
    if insts:
        for j in db.session.scalars(db.select(Job).where(Job.integration_id.in_([i.id for i in insts])).order_by(Job.id.desc()).limit(20)):
            jobs_.append(job_dict(j))
    from ..plugins import CAPABILITIES
    d = spec.as_dict()
    d.pop("path", None) if not p.is_admin else None
    return jsonify(d | {"instances": [instance_dict(i) for i in insts], "jobs": jobs_,
                        "capability_labels": {k: CAPABILITIES[k] for k in spec.capabilities},
                        "installed": catalog.is_installed(spec),
                        "can_add": p.can_any("integration", "admin") and catalog.is_installed(spec), "can_install": bool(p.is_admin)})


# ------------------------------------------------------------------ instances
@bp.get("/integrations")
def list_integrations():
    p = g.principal
    rows = db.session.scalars(db.select(Integration).order_by(Integration.name))
    return jsonify([instance_dict(i) for i in rows if p.can("integration", "view", _facts(i.name))])


@bp.post("/integrations")
def create_integration():
    data = body()
    name = str(data.get("name", "")).strip().lower()
    spec = _reg().get(str(data.get("plugin_id", "")))
    errors = []
    if spec is None or spec.error:
        errors.append({"field": "plugin_id", "message": "unknown or broken plugin"})
    elif not catalog.is_installed(spec):
        errors.append({"field": "plugin_id", "message": f"{spec.name} isn't installed: install it from the Marketplace first"})
    from .. import demo
    if spec is not None and demo.enabled() and spec.id not in demo.PLUGINS:
        errors.append({"field": "plugin_id", "message": "not in the demo: it doesn't connect anywhere"})
    if not NAME_RE.match(name):
        errors.append({"field": "name", "message": "lowercase letters, digits and dashes"})
    elif db.session.scalar(db.select(Integration).where(Integration.name == name)):
        errors.append({"field": "name", "message": "already exists"})
    if errors:
        return err(422, "validation failed", errors)
    rbac.require("integration", "admin", _facts(name))
    integ = Integration(plugin_id=spec.id, name=name, enabled=False, settings={}, state={},
                        selectors=[str(x) for x in spec.manifest.get("applies_to") or ["*"]])  # a plugin may start narrower (SNMP: tag:snmp)
    stored, _ = coerce_settings(spec, {}, {}, _vault())  # defaults; required ones are checked on enable
    integ.settings = stored
    db.session.add(integ)
    db.session.flush()
    audit.record("create", "integration", name, None, {"plugin": spec.id})
    db.session.commit()
    return jsonify(instance_dict(integ, full=True)), 201


@bp.get("/integrations/<name>")
def get_integration(name):
    return jsonify(instance_dict(_instance(name), full=True))


@bp.put("/integrations/<name>")
def update_integration(name):
    integ = _instance(name, "admin")
    spec = _reg().get(integ.plugin_id)
    if spec is None or spec.error:
        return err(409, "the plugin isn't available")
    data = body()
    before = {"enabled": integ.enabled, "selectors": list(integ.selectors or []), **public_settings(spec, integ.settings or {})[0]}
    if "selectors" in data:
        sel = data["selectors"]
        if isinstance(sel, str):
            sel = sel.replace(",", " ").split()
        sel = [str(x).strip() for x in sel or [] if str(x).strip()]
        bad = rbac.validate_scopes(sel)
        if bad:
            return err(422, "validation failed", [{"field": "selectors", "message": f"invalid: {', '.join(bad)} (use *, zone:, area:, network:, tag:, name:)"}])
        integ.selectors = sel
    if "settings" in data:
        stored, errors = coerce_settings(spec, data["settings"] or {}, integ.settings or {}, _vault())
        if errors and (data.get("enabled", integ.enabled)):
            return err(422, "validation failed", errors)
        integ.settings = stored
    if "enabled" in data:
        if data["enabled"]:
            _, errors = coerce_settings(spec, {}, integ.settings or {}, _vault())
            if errors:
                return err(422, "fill in the required settings before enabling", errors)
        integ.enabled = bool(data["enabled"])
    after = {"enabled": integ.enabled, "selectors": list(integ.selectors or []), **public_settings(spec, integ.settings or {})[0]}
    changed_secrets = [f["key"] for f in spec.settings if f.get("type") == "secret" and (data.get("settings") or {}).get(f["key"])]
    audit.record("update", "integration", name, before, after,
                 summary=(audit._summary("update", "", "", before, after) + (f"; secrets changed: {', '.join(changed_secrets)}" if changed_secrets else "")))
    db.session.commit()
    if before["selectors"] != after["selectors"]:
        jobs.emit("hostmodule.updated")
    return jsonify(instance_dict(integ, full=True))


@bp.delete("/integrations/<name>")
def delete_integration(name):
    integ = _instance(name, "admin")
    audit.record("delete", "integration", name, {"plugin": integ.plugin_id, "enabled": integ.enabled}, None)
    db.session.delete(integ)
    db.session.commit()
    return "", 204


# -------------------------------------------------------------------- actions
@bp.post("/integrations/<name>/actions/<action>")
def run_action(name, action):
    integ = _instance(name, "apply")
    if not integ.enabled:
        return err(409, "enable the integration first")
    data = request.get_json(silent=True) or {}
    job = jobs.start(integ, action, params=data.get("params") or {}, created_by=g.principal.label)
    db.session.expire_all()
    return jsonify(job_dict(db.session.get(Job, job.id), full=True)), 202


@bp.get("/integrations/<name>/export/<action>")
def export(name, action):
    integ = _instance(name, "apply")  # exports ignore scopes, so they need apply rights
    if not integ.enabled:
        return err(409, "enable the module first")
    spec = _reg().get(integ.plugin_id)
    a = spec.action(action) if spec else None
    if a is None or a["mode"] != "export":
        return err(404, f"no export '{action}'")
    ctx = Context(integ.name, spec, resolved_settings(spec, integ.settings or {}, _vault()), lambda m: None, integration=integ)
    fn = getattr(spec.plugin_class()(ctx), f"export_{action}", None) if spec.plugin_class() else None
    if fn is None:
        raise PluginError(f"plugin has no export_{action}()")
    f = fn(dict(request.args))
    return text_response(f.body, f.mimetype, f.filename)


@bp.get("/integrations/<name>/endpoints/<endpoint>")
def plugin_endpoint(name, endpoint):
    integ = _instance(name, "view")
    if not integ.enabled:
        return err(409, "the module is disabled")
    spec = _reg().get(integ.plugin_id)
    if spec is None or endpoint not in {e["id"] for e in spec.endpoints}:
        return err(404, f"no endpoint '{endpoint}'")
    ctx = Context(integ.name, spec, resolved_settings(spec, integ.settings or {}, _vault()), lambda m: None, integration=integ)
    fn = getattr(spec.plugin_class()(ctx), f"endpoint_{endpoint}", None) if spec.plugin_class() else None
    if fn is None:
        raise PluginError(f"plugin has no endpoint_{endpoint}()")
    return jsonify(fn(dict(request.args)))


# ----------------------------------------------------------------------- jobs
def _job(job_id: int, action="view") -> Job:
    j = db.session.get(Job, job_id)
    name = j.integration.name if j and j.integration else ""
    if j is None or not g.principal.can("integration", "view", _facts(name)):
        from flask import abort
        abort(404, description="no such job")
    rbac.require("integration", action, _facts(name))
    return j


@bp.get("/jobs")
def list_jobs():
    p = g.principal
    q = db.select(Job).order_by(Job.id.desc())
    if request.args.get("status"):
        q = q.where(Job.status == request.args["status"])
    if request.args.get("integration"):
        integ = db.session.scalar(db.select(Integration).where(Integration.name == request.args["integration"]))
        q = q.where(Job.integration_id == (integ.id if integ else -1))
    if request.args.get("host"):
        from ..models import JobHost
        q = q.where(Job.id.in_(db.select(JobHost.job_id).where(JobHost.host == request.args["host"])))
    if request.args.get("before"):
        q = q.where(Job.id < int(request.args["before"]))
    limit = min(int(request.args.get("limit", 50)), 200)
    out = []
    for j in db.session.scalars(q.limit(limit * 5)):
        if j.integration and p.can("integration", "view", _facts(j.integration.name)):
            out.append(job_dict(j))
            if len(out) >= limit:
                break
    resp = jsonify(out)
    if len(out) >= limit:
        resp.headers["X-Next-Before"] = str(out[-1]["id"])
    return resp


@bp.get("/jobs/queue")
def job_queue():
    """Who runs jobs: the web server itself, or `labdex worker` processes, and how many wait."""
    return jsonify(jobs.queue_status())


@bp.get("/jobs/<int:job_id>")
def get_job(job_id):
    return jsonify(job_dict(_job(job_id), full=True))


@bp.post("/jobs/<int:job_id>/approve")
@bp.post("/jobs/<int:job_id>/apply")  # before 0.6.1: apply = the one approval needed
def approve_job(job_id):
    j = _job(job_id, "apply")
    data = request.get_json(silent=True) or {}
    outcome = jobs.approve(j, g.principal, str(data.get("comment") or ""), skip=data.get("skip"))
    db.session.expire_all()
    return jsonify(job_dict(db.session.get(Job, job_id), full=True) | {"outcome": outcome}), 202


@bp.post("/jobs/<int:job_id>/discard")
def discard_job(job_id):
    j = _job(job_id, "apply")
    data = request.get_json(silent=True) or {}
    jobs.discard(j, g.principal.label, str(data.get("comment") or ""))
    return jsonify(job_dict(j, full=True))
