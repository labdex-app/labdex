"""Workloads: the Compose stacks that compose integrations keep, across integrations.

A stack lives in its integration's `stacks` setting. Here it can be read, its compose file
changed (that is a change to labdex, recorded like an integration update), and its deploy
planned through the integration's own action, so it waits on Changes for an approval."""
from __future__ import annotations

import yaml
from flask import g, jsonify

from .. import audit, composefiles, rbac
from ..models import Integration, Job, db
from . import body, bp, err

PLUGIN = "compose"


def _facts(name: str) -> dict:
    return rbac.named_facts(name)


def _raw(integ: Integration) -> list:
    """The stacks as written, without the checks: a broken entry still shows up to be fixed."""
    try:
        data = yaml.safe_load((integ.settings or {}).get("stacks") or "") or []
    except yaml.YAMLError:
        return []
    if isinstance(data, dict):
        data = data.get("stacks") or []
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def _services(compose) -> list[dict]:
    svcs = (compose or {}).get("services") if isinstance(compose, dict) else None
    out = []
    for name, s in (svcs or {}).items() if isinstance(svcs, dict) else []:
        s = s if isinstance(s, dict) else {}
        out.append({"name": str(name), "image": str(s.get("image") or ""), "ports": [str(p) for p in s.get("ports") or []][:6]})
    return out


def _last_deploy(integ: Integration, name: str) -> dict | None:
    for j in db.session.scalars(db.select(Job).where(Job.integration_id == integ.id, Job.action == "deploy").order_by(Job.id.desc()).limit(20)):
        only = (j.params or {}).get("stacks") or []
        if not only or name in only:
            at = j.finished_at or j.created_at
            return {"id": j.id, "status": j.status, "at": at.isoformat(timespec="seconds") + "Z" if at else None}
    return None


def _row(integ: Integration, s: dict, error: str = "") -> dict:
    p = g.principal
    f = _facts(integ.name)
    return {"integration": integ.name, "site": integ.site or "", "enabled": integ.enabled, "name": str(s.get("name") or ""),
            "host": str(s.get("host") or ""), "services": _services(s.get("compose")), "env": sorted(str(k) for k in (s.get("env") or {})),
            "can_edit": p.can("integration", "admin", f), "can_plan": integ.enabled and p.can("integration", "apply", f),
            "last_deploy": _last_deploy(integ, str(s.get("name") or "")), "error": error}


def _integrations():
    for integ in db.session.scalars(db.select(Integration).where(Integration.plugin_id == PLUGIN).order_by(Integration.name)):
        if g.principal.can("integration", "view", _facts(integ.name)):
            yield integ


@bp.get("/workloads")
def list_workloads():
    """Every Compose stack in the compose integrations you may see: host, services, last deploy."""
    out, problems = [], []
    for integ in _integrations():
        try:
            composefiles.parse((integ.settings or {}).get("stacks") or "")
        except composefiles.StackError as exc:
            problems.append({"integration": integ.name, "errors": exc.errors[:5]})
        out += [_row(integ, s) for s in _raw(integ)]
    return jsonify({"stacks": out, "problems": problems})


def _find(integration: str, host: str, name: str):
    integ = db.session.scalar(db.select(Integration).where(Integration.name == integration, Integration.plugin_id == PLUGIN))
    if integ is None or not g.principal.can("integration", "view", _facts(integration)):
        return None, None, None
    stacks = _raw(integ)
    for i, s in enumerate(stacks):
        if str(s.get("name") or "") == name and str(s.get("host") or "") == host:
            return integ, stacks, i
    return integ, stacks, None


def _compose_text(compose) -> str:
    return yaml.safe_dump(compose or {}, sort_keys=False, allow_unicode=True, width=120)


@bp.get("/workloads/<integration>/<host>/<name>")
def get_workload(integration, host, name):
    """One stack: its compose file as YAML, its services and its last deploy."""
    integ, stacks, i = _find(integration, host, name)
    if i is None:
        return err(404, f"no stack '{name}' on {host} in {integration}")
    return jsonify(_row(integ, stacks[i]) | {"compose": _compose_text(stacks[i].get("compose"))})


@bp.put("/workloads/<integration>/<host>/<name>")
def put_workload(integration, host, name):
    """Change the stack's compose file: {"compose": "services: …", "dry_run": bool}. Checked like
    the integration's settings and recorded as an update of the integration; nothing is deployed
    until a deploy is planned and approved. A dry run answers {"before", "after"}."""
    integ, stacks, i = _find(integration, host, name)
    if i is None:
        return err(404, f"no stack '{name}' on {host} in {integration}")
    rbac.require("integration", "admin", _facts(integration))
    data = body()
    try:
        compose = yaml.safe_load(str(data.get("compose") or ""))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return err(422, "validation failed", [{"field": "compose", "message": f"not valid YAML{f' (line {mark.line + 1})' if mark else ''}"}])
    before = _compose_text(stacks[i].get("compose"))
    stacks[i] = {**stacks[i], "compose": compose}
    try:
        composefiles.validate(stacks)
    except composefiles.StackError as exc:
        return err(422, "validation failed", [{"field": "compose", "message": e["message"]} for e in exc.errors[:5]])
    after = _compose_text(compose)
    if data.get("dry_run"):
        return jsonify({"before": before, "after": after})
    if before != after:
        settings = dict(integ.settings or {})
        settings["stacks"] = yaml.safe_dump(stacks, sort_keys=False, allow_unicode=True, width=120)
        integ.settings = settings
        audit.record("update", "integration", integ.name, {"stack": name, "compose": before}, {"stack": name, "compose": after},
                     summary=f"changed the compose file of stack {name} on {host}")
        db.session.commit()
    return jsonify(_row(integ, stacks[i]) | {"compose": after})
