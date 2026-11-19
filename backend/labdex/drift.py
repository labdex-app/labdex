"""Drift: does each system still match labdex?

Every plan answers that question for one integration and action: no changes means the system
matches labdex, changes mean it differs (someone changed it by hand, or labdex changed and wasn't
applied yet). A plan action on a schedule (its "Run every" setting, nightly for example) is the
drift check: when a scheduled plan finds changes after one that found none, `drift.found` goes
out; when it finds none again, `drift.cleared`. Nothing is stored apart from the jobs themselves.
"""
from __future__ import annotations

from flask import current_app

from .models import Integration, Job, db

PLAN_DONE = ("planned", "approved", "applying", "applied", "succeeded", "stale", "discarded", "failed")


def _iso(t):
    return t.isoformat() + "Z" if t else None


def _changes(job: Job) -> int:
    return len([c for c in job.plan or [] if not c.get("skipped")])


def _scheduled_plans(integ_id: int, action: str, before_id: int | None = None, limit: int = 50) -> list[Job]:
    q = db.select(Job).where(Job.integration_id == integ_id, Job.action == action, Job.mode == "plan",
                             Job.trigger == "schedule", Job.status.in_(PLAN_DONE))
    if before_id is not None:
        q = q.where(Job.id < before_id)
    return list(db.session.scalars(q.order_by(Job.id.desc()).limit(limit)))


def _found(job: Job) -> bool | None:
    """What a finished plan said: True = differs, False = matches, None = it failed."""
    if job.status == "failed":
        return None
    return bool(job.plan) and job.status != "succeeded"


def note(job: Job) -> str | None:
    """After a scheduled plan: notify when the answer changed. Returns the event sent, if any."""
    if job is None or job.mode != "plan" or job.trigger != "schedule" or job.integration is None:
        return None
    now = _found(job)
    if now is None:
        return None
    prev = next((p for p in _scheduled_plans(job.integration_id, job.action, before_id=job.id) if _found(p) is not None), None)
    was = _found(prev) if prev is not None else False
    if now == was:
        return None
    from . import notify
    name = job.integration.name
    label = _label(job.integration, job.action)
    data = {"job": job.id, "integration": name, "action": job.action, "changes": _changes(job)}
    try:
        if now:
            notify.emit("drift.found", f"Drift: {name} differs from labdex",
                        f"{label}: {_changes(job)} change(s) to bring it back in line. Review the plan before applying it.",
                        f"/jobs/{job.id}", data, integration=name)
            return "drift.found"
        notify.emit("drift.cleared", f"{name} matches labdex again", f"{label}: nothing to change.", f"/jobs/{job.id}", data, integration=name)
        return "drift.cleared"
    except Exception:  # a notification never breaks a job
        db.session.rollback()
        current_app.logger.exception("queueing a drift notification failed")
        return None


def _label(integ: Integration, action: str) -> str:
    spec = current_app.extensions["labdex"]["registry"].get(integ.plugin_id)
    a = spec.action(action) if spec else None
    return (a or {}).get("label") or action


def state(visible=lambda name: True) -> dict:
    """Every plan action of every integration: matches, differs, failed or not checked yet."""
    reg = current_app.extensions["labdex"]["registry"]
    items = []
    for integ in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
        spec = reg.get(integ.plugin_id)
        if spec is None or spec.error or not visible(integ.name):
            continue
        for a in spec.actions:
            if a.get("mode") != "plan" or a.get("scope") == "host":
                continue
            every = int((integ.settings or {}).get(f"schedule_{a['id']}") or 0) if a.get("schedulable") else 0
            last = db.session.scalar(db.select(Job).where(
                Job.integration_id == integ.id, Job.action == a["id"], Job.mode == "plan", Job.status.in_(PLAN_DONE))
                .order_by(Job.id.desc()).limit(1))
            item = {"integration": integ.name, "site": integ.site or "", "plugin": integ.plugin_id, "action": a["id"],
                    "label": a.get("label") or a["id"], "schedule_minutes": every, "state": "unchecked",
                    "changes": 0, "job_id": None, "checked_at": None, "since": None}
            if last is not None:
                item.update(job_id=last.id, checked_at=_iso(last.finished_at or last.created_at))
                if last.status == "failed":
                    item["state"] = "failed"
                    item["error"] = str((last.result or {}).get("error") or "")[:300]
                elif last.status == "applied":
                    item["state"] = "applied"  # labdex wrote it; the next check says whether it holds
                elif _found(last):
                    item.update(state="drift", changes=_changes(last))
                    # since the first plan in a row that found changes
                    since = last
                    for p in db.session.scalars(db.select(Job).where(
                            Job.integration_id == integ.id, Job.action == a["id"], Job.mode == "plan",
                            Job.status.in_(PLAN_DONE), Job.id < last.id).order_by(Job.id.desc()).limit(200)):
                        if _found(p) is not True:
                            break
                        since = p
                    item["since"] = _iso(since.created_at)
                else:
                    item["state"] = "in_sync"
            items.append(item)
    summary = {k: sum(1 for i in items if i["state"] == k) for k in ("in_sync", "drift", "applied", "failed", "unchecked")}
    summary["unscheduled"] = sum(1 for i in items if not i["schedule_minutes"])
    return {"items": items, "summary": summary}
