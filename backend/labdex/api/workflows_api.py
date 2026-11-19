"""Workflows and their runs: /api/workflows, /api/workflow-runs.

Rights follow the steps: running a workflow needs integration:apply on every integration its
steps use (and host:apply for a step on one host); saving one that runs on a schedule, unattended,
needs integration:admin on them. Each step that is a plan still waits for its own approvals.
"""
from __future__ import annotations

from flask import current_app, g, jsonify, request

from .. import audit, rbac, store, workflows
from ..models import Host, JobTemplate, Workflow, WorkflowRun, db
from ..plugins.base import PluginError
from . import body, bp, err


def _check(errors):
    if errors:
        raise store.ValidationError(errors)


def _404(what):
    from flask import abort
    abort(404, description=f"no such {what}")


def _integrations(steps) -> tuple[set[str], set[str]]:
    integs, hosts = set(), set()
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if s.get("template"):
            t = db.session.scalar(db.select(JobTemplate).where(JobTemplate.name == str(s["template"])))
            if t is not None:
                integs.add(t.integration.name)
                if t.host:
                    hosts.add(t.host)
        elif s.get("integration"):
            integs.add(str(s["integration"]))
            if s.get("host"):
                hosts.add(str(s["host"]))
    return integs, hosts


def _require(steps, scheduled: bool) -> None:
    integs, hosts = _integrations(steps)
    for name in sorted(integs):
        rbac.require("integration", "admin" if scheduled else "apply", rbac.named_facts(name))
    for name in sorted(hosts):
        h = db.session.scalar(db.select(Host).where(Host.name == name))
        if h is not None:
            rbac.require("host", "apply", rbac.host_facts(store.host_access(h)))


def _can_run(w: Workflow) -> bool:
    integs, _ = _integrations(w.steps)
    return all(g.principal.can("integration", "apply", rbac.named_facts(n)) for n in integs)


def _visible(steps) -> bool:
    integs, _ = _integrations(steps)
    return all(g.principal.can("integration", "view", rbac.named_facts(n)) for n in integs)


def _wf(wid: int) -> Workflow:
    w = db.session.get(Workflow, wid)
    if w is None or not _visible(w.steps):
        _404("workflow")
    return w


def _registry():
    return current_app.extensions["labdex"]["registry"]


@bp.get("/workflows")
def list_workflows():
    if not g.principal.can_any("integration", "view"):
        return err(403, "you lack integration:view")
    out = []
    for w in db.session.scalars(db.select(Workflow).order_by(Workflow.name)):
        if _visible(w.steps):
            out.append(workflows.to_dict(w) | {"can_run": _can_run(w)})
    return jsonify(out)


@bp.get("/workflows/<int:wid>")
def get_workflow(wid):
    w = _wf(wid)
    runs = db.session.scalars(db.select(WorkflowRun).where(WorkflowRun.workflow_id == w.id).order_by(WorkflowRun.id.desc()).limit(20))
    return jsonify(workflows.to_dict(w) | {"can_run": _can_run(w), "runs": [workflows.run_dict(r) for r in runs]})


@bp.post("/workflows")
def create_workflow():
    d = body()
    _require(d.get("steps"), bool(d.get("schedule")))
    w = Workflow(created_by=g.principal.label)
    _check(workflows.apply(w, d, _registry()))
    db.session.add(w)
    db.session.flush()
    audit.record("create", "workflow", w.name, after=workflows.to_dict(w))
    db.session.commit()
    return jsonify(workflows.to_dict(w)), 201


@bp.put("/workflows/<int:wid>")
def update_workflow(wid):
    w = _wf(wid)
    d = body()
    _require(w.steps, bool(w.schedule))
    before = workflows.to_dict(w)
    _check(workflows.apply(w, d, _registry()))
    _require(w.steps, bool(w.schedule))
    audit.record("update", "workflow", w.name, before=before, after=workflows.to_dict(w))
    db.session.commit()
    return jsonify(workflows.to_dict(w))


@bp.delete("/workflows/<int:wid>")
def delete_workflow(wid):
    w = _wf(wid)
    _require(w.steps, bool(w.schedule))
    audit.record("delete", "workflow", w.name, before=workflows.to_dict(w))
    db.session.delete(w)
    db.session.commit()
    return "", 204


@bp.post("/workflows/<int:wid>/run")
def run_workflow(wid):
    w = _wf(wid)
    _require(w.steps, False)
    try:
        r = workflows.start(w, trigger="manual", created_by=g.principal.label)
    except PluginError as exc:
        return err(409, str(exc))
    audit.record("run", "workflow", w.name, summary=f"started run #{r.id}", actor=(g.principal.label, "user"))
    db.session.commit()
    return jsonify(workflows.run_dict(r, full=True)), 202


# ------------------------------------------------------------------ runs
def _run(rid: int) -> WorkflowRun:
    r = db.session.get(WorkflowRun, rid)
    if r is None or not _visible(r.steps):
        _404("workflow run")
    return r


@bp.get("/workflow-runs")
def list_runs():
    if not g.principal.can_any("integration", "view"):
        return err(403, "you lack integration:view")
    q = db.select(WorkflowRun).order_by(WorkflowRun.id.desc()).limit(min(int(request.args.get("limit", 50)), 200))
    if request.args.get("status") == "open":
        q = q.where(WorkflowRun.status.in_(workflows.OPEN))
    elif request.args.get("status"):
        q = q.where(WorkflowRun.status == request.args["status"])
    return jsonify([workflows.run_dict(r) for r in db.session.scalars(q) if _visible(r.steps)])


@bp.get("/workflow-runs/<int:rid>")
def get_run(rid):
    r = _run(rid)
    workflows.advance(r)  # an answer as of now, not as of the last scheduler tick
    return jsonify(workflows.run_dict(r, full=True))


@bp.post("/workflow-runs/<int:rid>/cancel")
def cancel_run(rid):
    r = _run(rid)
    _require(r.steps, False)
    try:
        workflows.cancel(r, g.principal.label)
    except PluginError as exc:
        return err(409, str(exc))
    audit.record("cancel", "workflow", r.name, summary=f"cancelled run #{r.id}", actor=(g.principal.label, "user"))
    db.session.commit()
    return jsonify(workflows.run_dict(r, full=True))
