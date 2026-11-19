"""Workflows: steps in order, run by a click or on a schedule, inside a maintenance window.

A step is one of:

    {kind: template, template: <job template name>}
    {kind: action, integration: <name>, action: <id>, params: {...}, host: <name>}
    {kind: wait, minutes: 10}
    {kind: check, hosts: [...], timeout_minutes: 15}   the hosts answer again (empty: the hosts of the
                                                     steps before it); for after a reboot
    each with an optional name and on_failure: stop (default) | continue

A step that is a plan waits for its approvals like any other plan (approval policies, `approval:
always`, change windows); the run shows "waiting" until a person decides. A discarded plan fails
its step. A plan with nothing to change counts as done. With a maintenance window, a step only
starts while the window is open; a run that reaches the end of the window pauses and goes on at
the next opening.

tick() moves every unfinished run along; the scheduler calls it, and so does anything that wants
an answer now (the API after starting a run).
"""
from __future__ import annotations

import re
import threading
from datetime import timedelta

from . import approvals
from .models import ChangeWindow, Host, HostHealth, Integration, Job, JobTemplate, Workflow, WorkflowRun, db, utcnow
from .plugins.base import PluginError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 ._-]{0,63}$", re.I)
KINDS = ("template", "action", "wait", "check")
OPEN = ("running", "waiting")
DONE_JOB = ("succeeded", "applied")
FAILED_JOB = ("failed", "discarded")
MAX_STEPS = 30
_moving = threading.local()  # run ids being moved along in this thread (a step's job may finish inside start())


def _iso(t):
    return t.isoformat() + "Z" if t else None


# ------------------------------------------------------------------ validation
def _clean_step(i: int, s, registry) -> tuple[dict, list[dict]]:
    f = f"steps[{i}]"
    if not isinstance(s, dict):
        return {}, [{"field": f, "message": "a step: {kind, …}"}]
    errs = []
    kind = str(s.get("kind") or ("template" if s.get("template") else "action" if s.get("action") else ""))
    if kind not in KINDS:
        return {}, [{"field": f"{f}.kind", "message": "template, action, wait or check"}]
    out = {"kind": kind, "name": str(s.get("name") or "")[:80],
           "on_failure": str(s.get("on_failure") or "stop")}
    if out["on_failure"] not in ("stop", "continue"):
        errs.append({"field": f"{f}.on_failure", "message": "stop or continue"})
    if kind == "template":
        name = str(s.get("template") or "")
        if db.session.scalar(db.select(JobTemplate).where(JobTemplate.name == name)) is None:
            errs.append({"field": f"{f}.template", "message": f"no job template '{name}'"})
        out["template"] = name
        out["name"] = out["name"] or name
    elif kind == "action":
        integ = db.session.scalar(db.select(Integration).where(Integration.name == str(s.get("integration") or "")))
        action_id = str(s.get("action") or "")
        spec = registry.get(integ.plugin_id) if integ is not None else None
        action = spec.action(action_id) if spec else None
        if integ is None:
            errs.append({"field": f"{f}.integration", "message": "no such integration"})
        elif action is None or action.get("mode") == "export":
            errs.append({"field": f"{f}.action", "message": f"{integ.name} has no runnable action '{action_id}'"})
        host = str(s.get("host") or "")
        if action and action.get("scope") == "host":
            if db.session.scalar(db.select(Host).where(Host.name == host)) is None:
                errs.append({"field": f"{f}.host", "message": "this action runs for one host; name it"})
        else:
            host = ""
        params = s.get("params") or {}
        if not isinstance(params, dict):
            errs.append({"field": f"{f}.params", "message": "a mapping"})
            params = {}
        out.update(integration=str(s.get("integration") or ""), action=action_id, host=host, params=params)
        out["name"] = out["name"] or f"{out['integration']}: {(action or {}).get('label') or action_id}"
    elif kind == "wait":
        try:
            m = int(s.get("minutes") or 0)
        except (TypeError, ValueError):
            m = 0
        if not 1 <= m <= 24 * 60:
            errs.append({"field": f"{f}.minutes", "message": "1 to 1440"})
        out["minutes"] = m
        out["name"] = out["name"] or f"wait {m} min"
    else:  # check
        hosts = s.get("hosts") or []
        if not isinstance(hosts, list):
            errs.append({"field": f"{f}.hosts", "message": "a list of host names"})
            hosts = []
        hosts = [str(h) for h in hosts]
        known = set(db.session.scalars(db.select(Host.name).where(Host.name.in_(hosts)))) if hosts else set()
        if set(hosts) - known:
            errs.append({"field": f"{f}.hosts", "message": "no host " + ", ".join(sorted(set(hosts) - known))})
        try:
            t = int(s.get("timeout_minutes") or 15)
        except (TypeError, ValueError):
            t = 0
        if not 1 <= t <= 24 * 60:
            errs.append({"field": f"{f}.timeout_minutes", "message": "1 to 1440"})
        out.update(hosts=hosts, timeout_minutes=t)
        out["name"] = out["name"] or "hosts answer"
    return out, errs


def apply(w: Workflow, d: dict, registry) -> list[dict]:
    """Validate and copy the fields. Returns errors (nothing is changed when there are any)."""
    errs = []
    name = str(d.get("name", w.name or "")).strip()
    if not NAME_RE.match(name):
        errs.append({"field": "name", "message": "letters, digits, space . _ -; up to 64"})
    else:
        other = db.session.scalar(db.select(Workflow).where(Workflow.name == name))
        if other is not None and other.id != w.id:
            errs.append({"field": "name", "message": "a workflow with this name exists"})
    raw = d.get("steps", w.steps or [])
    steps = []
    if not isinstance(raw, list) or not raw:
        errs.append({"field": "steps", "message": "at least one step"})
    elif len(raw) > MAX_STEPS:
        errs.append({"field": "steps", "message": f"at most {MAX_STEPS} steps"})
    else:
        for i, s in enumerate(raw):
            clean, e = _clean_step(i, s, registry)
            steps.append(clean)
            errs += e
    sched = {k: d.get(k, getattr(w, k)) for k in ("schedule", "every_minutes", "days", "at", "timezone")}
    schedule = str(sched["schedule"] or "")
    if schedule not in ("", "every", "weekly"):
        errs.append({"field": "schedule", "message": "'', every or weekly"})
    every = int(sched["every_minutes"] or 0)
    if schedule == "every" and every < 15:
        errs.append({"field": "every_minutes", "message": "at least 15"})
    tz = str(sched["timezone"] or "UTC")
    if schedule == "weekly":
        werrs = approvals.validate_window({"name": "x", "days": sched["days"] or [], "start": sched["at"] or "",
                                           "end": sched["at"] or "", "timezone": tz})
        errs += [{"field": "at" if e["field"] in ("start", "end") else e["field"], "message": e["message"]} for e in werrs
                 if e["field"] != "end"]
    window_id = d.get("window_id", w.window_id)
    if "window" in d:  # by name, as in a YAML file
        win = db.session.scalar(db.select(ChangeWindow).where(ChangeWindow.name == str(d["window"] or ""))) if d["window"] else None
        if d["window"] and win is None:
            errs.append({"field": "window", "message": "no such change window"})
        window_id = win.id if win else None
    elif window_id not in (None, "", 0) and db.session.get(ChangeWindow, int(window_id)) is None:
        errs.append({"field": "window_id", "message": "no such change window"})
    if errs:
        return errs
    w.name, w.steps = name, steps
    w.description = str(d.get("description", w.description or ""))[:200]
    w.schedule, w.every_minutes, w.at, w.timezone = schedule, every, str(sched["at"] or ""), tz
    w.days = [x for x in approvals.DAYS if x in (sched["days"] or [])]
    w.window_id = int(window_id) if window_id not in (None, "", 0) else None
    w.enabled = bool(d.get("enabled", w.enabled if w.id else True))
    w.next_run_at = next_run(w)
    return []


def next_run(w: Workflow, after=None):
    """The next scheduled start; with a window, the first moment the window is open from then."""
    after = after or utcnow()
    if not w.enabled or not w.schedule:
        return None
    if w.schedule == "every":
        nxt = max((w.last_run_at or after) + timedelta(minutes=max(int(w.every_minutes or 0), 15)), after)
    else:
        win = {"days": w.days or approvals.DAYS, "start": w.at or "00:00", "end": w.at or "00:00", "timezone": w.timezone}
        starts = sorted(s for s, _ in approvals._spans(win, after) if s > after)
        if not starts:
            return None
        nxt = starts[0]
    if w.window is not None:
        nxt = approvals.next_open(approvals.window_dict(w.window), nxt) or nxt
    return nxt


# ------------------------------------------------------------------ views
def to_dict(w: Workflow) -> dict:
    last = db.session.scalar(db.select(WorkflowRun).where(WorkflowRun.workflow_id == w.id).order_by(WorkflowRun.id.desc()).limit(1))
    return {"id": w.id, "name": w.name, "description": w.description, "steps": list(w.steps or []),
            "schedule": w.schedule, "every_minutes": w.every_minutes, "days": list(w.days or []), "at": w.at,
            "timezone": w.timezone, "window": w.window.name if w.window else None, "window_id": w.window_id,
            "enabled": w.enabled, "next_run_at": _iso(w.next_run_at), "last_run_at": _iso(w.last_run_at),
            "created_by": w.created_by, "last_run": run_dict(last) if last else None}


def run_dict(r: WorkflowRun, full: bool = False) -> dict:
    """A run as the Changes page shows it: one line per step, with its job when it has one."""
    steps = []
    for n, s in enumerate(r.steps or []):
        st = s.get("state") or {}
        steps.append({"n": n, "name": s.get("name") or s.get("kind"), "kind": s.get("kind"),
                      "status": st.get("status", "pending"), "job_id": st.get("job_id"), "note": st.get("note", ""),
                      "started_at": st.get("started_at"), "finished_at": st.get("finished_at"),
                      **({"step": {k: v for k, v in s.items() if k != "state"}} if full else {})})
    return {"id": r.id, "workflow_id": r.workflow_id, "name": r.name, "status": r.status, "step": r.step,
            "of": len(r.steps or []), "trigger": r.trigger, "created_by": r.created_by, "note": r.note,
            "created_at": _iso(r.created_at), "finished_at": _iso(r.finished_at),
            "window": (r.window or {}).get("name"), "steps": steps}


def job_note(job: Job) -> dict | None:
    """For a job's dict: which workflow run and step it belongs to."""
    if not job.workflow_run_id:
        return None
    r = db.session.get(WorkflowRun, job.workflow_run_id)
    if r is None:
        return None
    n = next((i for i, s in enumerate(r.steps or []) if (s.get("state") or {}).get("job_id") == job.id), None)
    return {"run_id": r.id, "name": r.name, "step": n, "of": len(r.steps or []), "status": r.status}


# ------------------------------------------------------------------ running
def start(w: Workflow, *, trigger: str = "manual", created_by: str = "system") -> WorkflowRun:
    busy = db.session.scalar(db.select(WorkflowRun).where(WorkflowRun.workflow_id == w.id, WorkflowRun.status.in_(OPEN)))
    if busy is not None:
        raise PluginError(f"workflow '{w.name}' is already running (run #{busy.id})")
    if not w.steps:
        raise PluginError(f"workflow '{w.name}' has no steps")
    r = WorkflowRun(workflow_id=w.id, name=w.name, status="running", step=0,
                    steps=[dict(s, state={"status": "pending"}) for s in w.steps],
                    window=approvals.window_dict(w.window) if w.window else None, trigger=trigger, created_by=created_by)
    db.session.add(r)
    w.last_run_at = utcnow()
    w.next_run_at = next_run(w)
    db.session.commit()
    _emit(r, "workflow.started")
    advance(r)
    return r


def cancel(r: WorkflowRun, by: str) -> WorkflowRun:
    if r.status not in OPEN:
        raise PluginError(f"run #{r.id} is {r.status}")
    steps = [dict(s) for s in r.steps or []]
    for s in steps[r.step:]:
        st = dict(s.get("state") or {})
        if st.get("status") in ("pending", "running", "waiting"):
            job = db.session.get(Job, st["job_id"]) if st.get("job_id") else None
            if job is not None and job.status in ("planned", "approved"):
                job.status, job.finished_at = "discarded", utcnow()
                job.result = dict(job.result or {}, discarded_by=by, comment=f"workflow run #{r.id} cancelled")
            st["status"] = "cancelled"
            s["state"] = st
    r.steps, r.status, r.finished_at, r.note = steps, "cancelled", utcnow(), f"cancelled by {by}"
    db.session.commit()
    return r


def _emit(r: WorkflowRun, event: str, body: str = "") -> None:
    from . import notify
    try:
        title = {"workflow.started": f"Workflow {r.name} started", "workflow.succeeded": f"Workflow {r.name} finished",
                 "workflow.failed": f"Workflow {r.name} failed", "workflow.waiting": f"Workflow {r.name} waits for an approval"}[event]
        notify.emit(event, title, body, link=f"/changes?run={r.id}", data={"run": r.id, "workflow": r.name})
    except Exception:  # a notification never stops a workflow
        db.session.rollback()


def _set(r: WorkflowRun, n: int, **state) -> None:
    steps = [dict(s) for s in r.steps or []]
    steps[n]["state"] = {**(steps[n].get("state") or {}), **state}
    r.steps = steps


def _follow(job: Job) -> Job:
    """A plan that was made again (the inventory changed, or someone planned the action anew)
    continues in the newer plan."""
    seen = set()
    while job.status == "stale" and job.id not in seen:
        seen.add(job.id)
        nxt = (job.result or {}).get("replanned_as")
        newer = db.session.get(Job, nxt) if nxt else db.session.scalar(
            db.select(Job).where(Job.replaces == job.id).order_by(Job.id.desc()).limit(1))
        if newer is None:
            newer = db.session.scalar(db.select(Job).where(
                Job.integration_id == job.integration_id, Job.action == job.action, Job.id > job.id,
                Job.workflow_run_id == job.workflow_run_id).order_by(Job.id).limit(1)) if job.workflow_run_id else None
        if newer is None:
            break
        if job.workflow_run_id and newer.workflow_run_id is None:
            newer.workflow_run_id = job.workflow_run_id
        job = newer
    return job


def _start_job(r: WorkflowRun, s: dict) -> Job:
    from .plugins import jobs
    if s["kind"] == "template":
        t = db.session.scalar(db.select(JobTemplate).where(JobTemplate.name == s["template"]))
        if t is None:
            raise PluginError(f"job template '{s['template']}' no longer exists")
        integ, action, params = t.integration, t.action, dict(t.params or {})
        if t.host:
            params["host"] = t.host
        template_id = t.id
    else:
        integ = db.session.scalar(db.select(Integration).where(Integration.name == s["integration"]))
        if integ is None:
            raise PluginError(f"integration '{s['integration']}' no longer exists")
        action, params, template_id = s["action"], dict(s.get("params") or {}), None
        if s.get("host"):
            params["host"] = s["host"]
    if not integ.enabled:
        raise PluginError(f"integration '{integ.name}' is turned off")
    job = jobs.start(integ, action, trigger="schedule" if r.trigger == "schedule" else "manual", params=params,
                     created_by=f"workflow {r.name} (run #{r.id})", template_id=template_id, workflow_run_id=r.id)
    db.session.refresh(job)
    return job


def _check_hosts(r: WorkflowRun, n: int, s: dict) -> list[str]:
    if s.get("hosts"):
        return list(s["hosts"])
    names = set()
    for prev in (r.steps or [])[:n]:
        jid = (prev.get("state") or {}).get("job_id")
        job = db.session.get(Job, jid) if jid else None
        if job is not None:
            names |= {h.host for h in job.hosts}
    return sorted(names)


def advance(r: WorkflowRun, now=None) -> str:
    """Move a run along as far as it can go now. Returns its status."""
    busy = getattr(_moving, "ids", None)
    if busy is None:
        busy = _moving.ids = set()
    if r.id in busy:  # called again from a job that finished inside this call: the outer call goes on
        return r.status
    busy.add(r.id)
    try:
        return _advance(r, now or utcnow())
    finally:
        busy.discard(r.id)


def _advance(r: WorkflowRun, now) -> str:
    for _ in range(MAX_STEPS * 3):  # every pass finishes or starts a step, or returns
        if r.status not in OPEN:
            return r.status
        if r.step >= len(r.steps or []):
            r.status, r.finished_at, r.note = "succeeded", now, ""
            db.session.commit()
            _emit(r, "workflow.succeeded")
            return r.status
        n, s = r.step, (r.steps or [])[r.step]
        st = s.get("state") or {}
        status = st.get("status", "pending")

        if status == "pending":
            if r.window and not approvals.window_open(r.window, now):
                nxt = approvals.next_open(r.window, now)
                note = f"waiting for the maintenance window {r.window.get('name')}" + (f" (opens {_iso(nxt)})" if nxt else "")
                if r.status != "waiting" or r.note != note:
                    r.status, r.note = "waiting", note
                    db.session.commit()
                return r.status
            stamp = {"started_at": _iso(now)}
            if s["kind"] == "wait":
                _set(r, n, status="running", until=_iso(now + timedelta(minutes=int(s["minutes"]))), **stamp)
            elif s["kind"] == "check":
                _set(r, n, status="running", until=_iso(now + timedelta(minutes=int(s.get("timeout_minutes") or 15))),
                     hosts=_check_hosts(r, n, s), **stamp)
            else:
                try:
                    job = _start_job(r, s)
                except PluginError as exc:
                    _set(r, n, status="failed", note=str(exc)[:300], finished_at=_iso(now), **stamp)
                else:
                    _set(r, n, status="running", job_id=job.id, **stamp)
            r.status, r.note = "running", ""
            db.session.commit()
            continue

        if status in ("running", "waiting"):
            outcome, note = None, ""
            if s["kind"] == "wait":
                if now >= _parse(st["until"]):
                    outcome = "done"
            elif s["kind"] == "check":
                hosts = st.get("hosts") or []
                states = {h.host: h.state for h in db.session.scalars(db.select(HostHealth).where(HostHealth.host.in_(hosts)))}
                down = [h for h in hosts if states.get(h) != "up"]
                if not down:
                    outcome, note = "done", f"{len(hosts)} host(s) answer" if hosts else "no hosts to check"
                elif now >= _parse(st["until"]):
                    outcome, note = "failed", "not answering: " + ", ".join(down)
                else:
                    note = "waiting for " + ", ".join(down)
            else:
                job = db.session.get(Job, st.get("job_id"))
                if job is None:
                    outcome, note = "failed", "its job was deleted"
                else:
                    job = _follow(job)
                    if job.id != st.get("job_id"):
                        _set(r, n, job_id=job.id)
                    if job.status in DONE_JOB:
                        outcome = "done"
                        note = (job.result or {}).get("message", "") if job.status == "succeeded" else ""
                    elif job.status in FAILED_JOB or job.status == "stale":
                        outcome = "failed"
                        note = {"discarded": "the plan was discarded", "stale": "the plan went stale"}.get(
                            job.status, str((job.result or {}).get("error") or "the job failed"))[:300]
                    elif job.status in ("planned", "approved"):
                        if status != "waiting":
                            _set(r, n, status="waiting", note="waiting for an approval" if job.status == "planned" else "approved, waiting to apply")
                            r.status, r.note = "waiting", f"step {n + 1}: {s.get('name')} waits for an approval"
                            db.session.commit()
                            if job.status == "planned":
                                _emit(r, "workflow.waiting", f"Step {n + 1} of {len(r.steps)}: {s.get('name')} (plan #{job.id})")
                        return r.status
                    elif status == "waiting":  # approved and applying now
                        _set(r, n, status="running", note="")
                        r.status, r.note = "running", ""
                        db.session.commit()
            if outcome is None:
                if note and note != st.get("note"):
                    _set(r, n, note=note)
                    db.session.commit()
                return r.status
            _set(r, n, status=outcome, note=note, finished_at=_iso(now))
            status = outcome
            db.session.commit()

        if status in ("done", "skipped"):
            r.step += 1
            db.session.commit()
            continue
        if status == "failed":
            if s.get("on_failure") == "continue":
                r.step += 1
                db.session.commit()
                continue
            steps = [dict(x) for x in r.steps]
            for later in steps[n + 1:]:
                later["state"] = {**(later.get("state") or {}), "status": "skipped"}
            r.steps, r.status, r.finished_at = steps, "failed", now
            r.note = f"step {n + 1}: {s.get('name')} failed" + (f": {(r.steps[n].get('state') or {}).get('note')}" if (r.steps[n].get('state') or {}).get('note') else "")
            r.note = r.note[:300]
            db.session.commit()
            _emit(r, "workflow.failed", r.note)
            return r.status
        return r.status  # cancelled steps: nothing to do
    return r.status


def _parse(s: str):
    from datetime import datetime
    return datetime.fromisoformat(s.removesuffix("Z"))


def tick(now=None) -> dict:
    """Start scheduled workflows that are due, and move every open run along."""
    now = now or utcnow()
    started = []
    for w in list(db.session.scalars(db.select(Workflow).where(Workflow.enabled.is_(True), Workflow.schedule != "",
                                                               Workflow.next_run_at <= now))):
        try:
            started.append(start(w, trigger="schedule", created_by=f"schedule of {w.name}").id)
        except PluginError:
            w.next_run_at = next_run(w, now)  # still running from last time: the next slot
            db.session.commit()
    moved = 0
    for r in list(db.session.scalars(db.select(WorkflowRun).where(WorkflowRun.status.in_(OPEN)).order_by(WorkflowRun.id))):
        advance(r, now)
        moved += 1
    return {"started": started, "open": moved}
