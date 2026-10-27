"""Job templates: an action with saved parameters, run by a click or on a schedule."""
from __future__ import annotations

import re
from datetime import timedelta

from . import approvals
from .models import Host, Integration, JobTemplate, db, utcnow
from .plugins.base import PluginError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 ._-]{0,63}$", re.I)


def next_run(t: JobTemplate, after=None):
    after = after or utcnow()
    if not t.enabled or not t.schedule:
        return None
    if t.schedule == "every":
        nxt = (t.last_run_at or after) + timedelta(minutes=max(int(t.every_minutes or 0), 5))
        return max(nxt, after)
    win = {"days": t.days or approvals.DAYS, "start": t.at or "00:00", "end": t.at or "00:00", "timezone": t.timezone}
    starts = sorted(s for s, _ in approvals._spans(win, after) if s > after)
    return starts[0] if starts else None


def to_dict(t: JobTemplate, spec=None) -> dict:
    action = spec.action(t.action) if spec else None
    return {"id": t.id, "name": t.name, "integration": t.integration.name, "action": t.action,
            "action_label": (action or {}).get("label") or t.action, "mode": (action or {}).get("mode"),
            "host": t.host, "params": t.params or {}, "description": t.description, "schedule": t.schedule,
            "every_minutes": t.every_minutes, "days": list(t.days or []), "at": t.at, "timezone": t.timezone,
            "enabled": t.enabled, "next_run_at": t.next_run_at.isoformat() + "Z" if t.next_run_at else None,
            "last_run_at": t.last_run_at.isoformat() + "Z" if t.last_run_at else None, "last_job_id": t.last_job_id,
            "created_by": t.created_by}


def apply(t: JobTemplate, d: dict, registry, vault) -> list[dict]:
    """Validate and copy the fields. Returns errors (nothing is changed when there are any)."""
    from .plugins import coerce_fields
    errs = []
    name = str(d.get("name", t.name or "")).strip()
    if not NAME_RE.match(name):
        errs.append({"field": "name", "message": "letters, digits, space . _ -; up to 64"})
    else:
        other = db.session.scalar(db.select(JobTemplate).where(JobTemplate.name == name))
        if other is not None and other.id != t.id:
            errs.append({"field": "name", "message": "a template with this name exists"})
    integ = t.integration
    if "integration" in d or integ is None:
        integ = db.session.scalar(db.select(Integration).where(Integration.name == str(d.get("integration", ""))))
        if integ is None:
            errs.append({"field": "integration", "message": "no such integration"})
            return errs
    spec = registry.get(integ.plugin_id)
    action_id = str(d.get("action", t.action or ""))
    action = spec.action(action_id) if spec else None
    if action is None or action["mode"] == "export":
        errs.append({"field": "action", "message": "no runnable action with this id"})
        return errs
    host = str(d.get("host", t.host or "")).strip()
    if action.get("scope") == "host":
        if not host or db.session.scalar(db.select(Host).where(Host.name == host)) is None:
            errs.append({"field": "host", "message": "this action runs for one host; pick it"})
    else:
        host = ""
    params = d.get("params", t.params or {})
    clean = {}
    if action.get("params"):
        clean, perrs = coerce_fields(action["params"], params if isinstance(params, dict) else {}, {}, vault)
        errs += [{"field": f"params.{e['field']}", "message": e["message"]} for e in perrs]
    schedule = str(d.get("schedule", t.schedule or ""))
    if schedule not in ("", "every", "weekly"):
        errs.append({"field": "schedule", "message": "'', every or weekly"})
    every = int(d.get("every_minutes", t.every_minutes or 0) or 0)
    if schedule == "every" and every < 5:
        errs.append({"field": "every_minutes", "message": "at least 5"})
    days = d.get("days", t.days or [])
    at = str(d.get("at", t.at or ""))
    tz = str(d.get("timezone", t.timezone or "UTC") or "UTC")
    if schedule == "weekly":
        werrs = approvals.validate_window({"name": "x", "days": days, "start": at, "end": at, "timezone": tz})
        errs += [{"field": "at" if e["field"] in ("start", "end") else e["field"], "message": e["message"]} for e in werrs
                 if e["field"] != "end"]
    if errs:
        return errs
    t.name, t.integration_id, t.integration, t.action, t.host, t.params = name, integ.id, integ, action_id, host, clean
    t.description = str(d.get("description", t.description or ""))[:200]
    t.schedule, t.every_minutes, t.days, t.at, t.timezone = schedule, every, [x for x in approvals.DAYS if x in (days or [])], at, tz
    t.enabled = bool(d.get("enabled", t.enabled if t.id else True))
    t.next_run_at = next_run(t)
    return []


def run(t: JobTemplate, *, trigger: str, created_by: str):
    from .plugins import jobs
    params = dict(t.params or {})
    if t.host:
        params["host"] = t.host
    job = jobs.start(t.integration, t.action, trigger=trigger, params=params, created_by=created_by, template_id=t.id)
    t.last_run_at, t.last_job_id = utcnow(), job.id
    t.next_run_at = next_run(t)
    db.session.commit()
    return job


def due() -> list[int]:
    """Scheduled templates whose time has come (the scheduler calls this)."""
    started = []
    now = utcnow()
    for t in list(db.session.scalars(db.select(JobTemplate).where(JobTemplate.enabled.is_(True), JobTemplate.schedule != "",
                                                                  JobTemplate.next_run_at <= now))):
        if not t.integration.enabled:
            t.next_run_at = next_run(t, now)
            db.session.commit()
            continue
        try:
            started.append(run(t, trigger="schedule", created_by=f"template {t.name}").id)
        except PluginError:
            t.last_run_at = now  # busy or not runnable: try at the next slot
            t.next_run_at = next_run(t, now)
            db.session.commit()
    return started
