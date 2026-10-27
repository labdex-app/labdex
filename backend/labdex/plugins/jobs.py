"""Running plugin actions as jobs, plan → apply, event hooks and the scheduler.

Every action runs as a Job row with a log. Jobs are a queue in the database: a job
with status queued (or applying) and no worker is waiting. A dispatcher claims it
(sets `worker`), runs it in a thread and keeps `heartbeat_at` fresh; a job whose
worker stopped sending heartbeats is marked failed. The dispatcher runs inside the
web server (LABDEX_WORKER=inline, the default) or in its own process
(`labdex worker`, with LABDEX_WORKER=external on the web server). Actions in `plan` mode stop at
status `planned` with a list of changes; a user with integration:apply on that
integration applies them. A plan remembers the inventory revision it was made
from and refuses to apply after the inventory changed (re-plan instead), and a
newer plan for the same action marks older ones stale.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from fnmatch import fnmatchcase

from flask import Flask, current_app

from .. import audit
from ..models import Integration, Job, JobHost, Worker, db, utcnow
from . import resolved_settings
from .base import Change, Context, PluginError

ACTIVE = ("queued", "running", "applying")
WAITING = ("queued", "applying")  # with worker IS NULL: in the queue
HEARTBEAT = 10  # seconds between heartbeats
STALE = 90  # a job without a heartbeat for this long lost its worker
_lock = threading.Lock()
_wake = threading.Event()


def ext(app: Flask | None = None) -> dict:
    return (app or current_app).extensions["labdex"]


# ------------------------------------------------------------------ plumbing
class _JobLog:
    """Buffers log lines and flushes them to the job row about once a second."""

    def __init__(self, job_id: int):
        self.job_id = job_id
        self.buf: list[str] = []
        self.last = time.monotonic()

    def __call__(self, line: str):
        self.buf.append(f"[{utcnow().strftime('%H:%M:%S')}] {line}")
        if time.monotonic() - self.last > 1.0:
            self.flush()

    def flush(self):
        if not self.buf:
            return
        job = db.session.get(Job, self.job_id)
        job.log = (job.log or "") + "\n".join(self.buf) + "\n"
        self.buf.clear()
        self.last = time.monotonic()
        db.session.commit()


def _instance(job: Job):
    integ = job.integration
    spec = ext()["registry"].get(integ.plugin_id)
    if spec is None or spec.error:
        raise PluginError(f"plugin '{integ.plugin_id}' isn't available: {spec.error if spec else 'not installed'}")
    settings = resolved_settings(spec, integ.settings or {}, ext()["vault"])
    return integ, spec, settings


def _plugin_obj(spec, ctx):
    cls = spec.plugin_class()
    return cls(ctx) if cls else None


def _changes_from(raw) -> list[Change]:
    out = []
    for c in raw or []:
        out.append(c if isinstance(c, Change) else Change(**{k: v for k, v in c.items() if k != "skipped"}))
    return out


def _note_hosts(job: Job, names) -> None:
    have = {h.host for h in job.hosts}
    for n in sorted(set(names) - have):
        job.hosts.append(JobHost(host=str(n)[:63]))


# ---------------------------------------------------------------- execution
def _execute(job_id: int, phase: str) -> None:
    job = db.session.get(Job, job_id)
    log = _JobLog(job_id)
    try:
        integ, spec, settings = _instance(job)
        action = spec.action(job.action)
        ctx = Context(integ.name, spec, settings, log, job_id, integration=integ)
        obj = _plugin_obj(spec, ctx)
        job.status = "applying" if phase == "apply" else "running"
        job.started_at = job.started_at or utcnow()
        db.session.commit()
        log(f"{phase} {spec.id}.{job.action} on '{integ.name}' ({job.trigger})"
            + (f" for host {job.params['host']}" if job.params.get("host") else ""))
        limit = job.params.get("limit") or job.params.get("host") or settings.get("limit") or None
        if action.get("ansible") and spec.per_host and not job.params.get("host"):
            from .. import hostmodules
            names = [h["name"] for h in hostmodules.enabled_hosts(integ)]
            if not names:
                raise PluginError("no host is selected for this integration (check its 'applies to')")
            limit = ":".join(names) + (f":&{settings['limit']}" if settings.get("limit") else "")
            _note_hosts(job, names)

        if phase == "plan":
            if action.get("ansible"):
                res = ctx.ansible(action["ansible"], extravars=job.params, check=True, diff=True,
                                  limit=limit)
                if not res.ok:
                    raise PluginError(f"check run failed (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable})")
                changes = [Change("ansible", "task", spec.id, f"{res.changed} task(s) would change; see the log for the diff")] if res.changed else []
            else:
                fn = getattr(obj, f"plan_{job.action}", None)
                if fn is None:
                    raise PluginError(f"plugin has no plan_{job.action}()")
                changes = _changes_from(fn(job.params))
            job.plan = [c.as_dict() for c in changes]
            job.status = "planned" if changes else "succeeded"
            if changes:
                from .. import approvals
                job.fingerprint = approvals.fingerprint(job.plan)
                job.policy = approvals.match(job)
                approvals.carry_over(job)
            job.result = {"changes": len(changes)} if changes else {"changes": 0, "message": "Nothing to change"}
            log(f"{len(changes)} change(s) planned" if changes else "nothing to change")

        elif phase == "apply":
            if action.get("ansible"):
                res = ctx.ansible(action["ansible"], extravars=job.params, diff=True,
                                  limit=limit)
                if not res.ok:
                    raise PluginError(f"run failed (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable})")
                result = {"changed": res.changed}
            else:
                fn = getattr(obj, f"apply_{job.action}", None)
                if fn is None:
                    raise PluginError(f"plugin has no apply_{job.action}()")
                todo = [c for c in job.plan or [] if not c.get("skipped")]
                if len(todo) < len(job.plan or []):
                    log(f"leaving out {len(job.plan) - len(todo)} change(s) unticked at approval")
                result = fn(_changes_from(todo), job.params) or {}
            job.status, job.result = "applied", result
            done = len([c for c in job.plan or [] if not c.get("skipped")])
            audit.record("apply", "integration", integ.name, summary=f"applied {job.action} (job #{job.id}): {done} change(s)",
                         actor=(job.applied_by or "system", "user"))

        else:  # run
            if action.get("ansible"):
                res = ctx.ansible(action["ansible"], extravars=job.params,
                                  limit=limit)
                if not res.ok:
                    raise PluginError(f"run failed (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable})")
                result = {"changed": res.changed}
            elif job.action == "test" and hasattr(obj, "test"):
                result = {"message": obj.test()}
            else:
                fn = getattr(obj, f"run_{job.action}", None)
                if fn is None:
                    raise PluginError(f"plugin has no run_{job.action}()")
                result = fn(job.params) or {}
            job.status, job.result = "succeeded", result
    except PluginError as exc:
        db.session.rollback()
        job = db.session.get(Job, job_id)
        log(f"error: {exc}")
        job.status, job.result = "failed", {"error": str(exc)}
    except Exception as exc:  # plugin bug: keep labdex alive, keep the trace in the log
        db.session.rollback()
        job = db.session.get(Job, job_id)
        log("unexpected error:\n" + traceback.format_exc())
        job.status, job.result = "failed", {"error": f"{type(exc).__name__}: {exc}"}
    job.finished_at = utcnow()
    db.session.commit()
    log.flush()
    if job.status in ("failed", "applied"):
        _notify(job, "job.failed" if job.status == "failed" else "plan.applied")


def _notify(job: Job, event: str) -> None:
    from .. import notify
    try:
        notify.job_event(job, event)
    except Exception:  # a notification must never break a job
        db.session.rollback()
        current_app.logger.exception("queueing a notification failed")


def wake() -> None:
    """Something is waiting (a job, a notification): don't wait for the next poll."""
    _wake.set()


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _claim(job_id: int, who: str) -> str | None:
    """Take a waiting job. Returns its phase, or None when someone else has it."""
    res = db.session.execute(db.update(Job).where(Job.id == job_id, Job.worker.is_(None), Job.status.in_(WAITING))
                             .values(worker=who, heartbeat_at=utcnow()))
    db.session.commit()
    if res.rowcount != 1:
        return None
    job = db.session.get(Job, job_id)
    db.session.refresh(job)
    return job.phase or ("apply" if job.status == "applying" else job.mode if job.mode in ("plan", "run") else "run")


def _run_claimed(app: Flask, job_id: int, phase: str) -> None:
    with app.app_context():
        try:
            _execute(job_id, phase)
            if phase == "plan":
                _after_plan(job_id)
            if phase != "apply":
                _rerun_if_pending(job_id)
        finally:
            db.session.remove()


def _after_plan(job_id: int) -> None:
    """A fresh plan: apply it if its policy needs no (more) approvals."""
    from .. import approvals
    db.session.expire_all()
    job = db.session.get(Job, job_id)
    if job is None or job.status != "planned":
        return
    if not approvals.ready(job):
        _notify(job, "plan.waiting")
        return
    how = "carried over from the previous plan (same changes)" if job.approvals else "not needed (policy " + (job.policy or {}).get("name", "default") + ")"
    _append_log(job, f"approvals {how}")
    approvals.advance(job)


def _append_log(job: Job, line: str) -> None:
    job.log = (job.log or "") + f"[{utcnow().strftime('%H:%M:%S')}] {line}\n"
    db.session.commit()


def _submit(app: Flask, job_id: int) -> None:
    """A job was queued. Run it now (tests, CLI), or wake the dispatcher."""
    if app.config.get("LABDEX_JOBS_SYNC"):
        phase = _claim(job_id, worker_id())
        if phase:
            _run_claimed(app, job_id, phase)
    else:
        _wake.set()


class Dispatcher:
    """Claims queued jobs and runs them in threads; heartbeats; scheduler ticks."""

    def __init__(self, app: Flask, mode: str, slots: int = 2, scheduler: bool = True):
        self.app, self.mode, self.slots, self.scheduler = app, mode, slots, scheduler
        self.id = worker_id()
        self.pool = ThreadPoolExecutor(max_workers=slots, thread_name_prefix="labdex-job")
        self.running: set[int] = set()
        self.mu = threading.Lock()
        self.last_beat = 0.0
        self.last_tick = time.monotonic()
        self.stopping = False

    def start_thread(self) -> None:
        threading.Thread(target=self.loop, name="labdex-dispatcher", daemon=True).start()

    def loop(self, max_seconds: float | None = None) -> None:
        with self.app.app_context():
            self._recover_own()
        until = time.monotonic() + max_seconds if max_seconds else None
        while not self.stopping and (until is None or time.monotonic() < until):
            try:
                self.step()
            except Exception:  # never let the dispatcher die
                self.app.logger.exception("dispatcher step failed")
            _wake.wait(1.0)
            _wake.clear()

    def step(self) -> int:
        """One round: heartbeat, recover lost jobs, claim what fits, scheduler. Returns jobs started."""
        started = 0
        with self.app.app_context():
            try:
                now = time.monotonic()
                if now - self.last_beat >= HEARTBEAT:
                    self.last_beat = now
                    self._heartbeat()
                    recover_stale()
                with self.mu:
                    free = self.slots - len(self.running)
                if free > 0:
                    ids = db.session.scalars(db.select(Job.id).where(Job.worker.is_(None), Job.status.in_(WAITING))
                                             .order_by(Job.id).limit(free)).all()
                    for jid in ids:
                        phase = _claim(jid, self.id)
                        if phase:
                            with self.mu:
                                self.running.add(jid)
                            self.pool.submit(self._run, jid, phase)
                            started += 1
                _deliver(self.app)
                if now - self.last_tick >= 30:
                    self.last_tick = now
                    _due(self.app)
                    if self.scheduler:
                        _tick(self.app)
            finally:
                db.session.remove()
        return started

    def _run(self, job_id: int, phase: str) -> None:
        try:
            _run_claimed(self.app, job_id, phase)
        finally:
            with self.mu:
                self.running.discard(job_id)
            _wake.set()

    def _heartbeat(self) -> None:
        with self.mu:
            ids = list(self.running)
        t = utcnow()
        if ids:
            db.session.execute(db.update(Job).where(Job.id.in_(ids), Job.worker == self.id).values(heartbeat_at=t))
        w = db.session.get(Worker, self.id)
        if w is None:
            from .. import __version__
            w = Worker(id=self.id, mode=self.mode, version=__version__, started_at=t)
            db.session.add(w)
        w.seen_at, w.running, w.mode = t, len(ids), self.mode
        db.session.commit()

    def _recover_own(self) -> None:
        """Jobs this worker id held before a restart can't resume (a container's PIDs repeat)."""
        for job in db.session.scalars(db.select(Job).where(Job.worker == self.id, Job.status.in_(ACTIVE))):
            _fail_lost(job, "interrupted by a restart")
        db.session.commit()
        db.session.remove()


def _fail_lost(job: Job, why: str) -> None:
    job.status, job.result, job.finished_at = "failed", {"error": why}, utcnow()
    job.log = (job.log or "") + f"[{utcnow().strftime('%H:%M:%S')}] error: {why}\n"


def recover_stale() -> int:
    """Fail jobs whose worker stopped sending heartbeats."""
    cutoff = utcnow() - timedelta(seconds=STALE)
    n = 0
    for job in db.session.scalars(db.select(Job).where(Job.worker.is_not(None), Job.status.in_(ACTIVE),
                                                       Job.heartbeat_at < cutoff)):
        _fail_lost(job, f"its worker ({job.worker}) stopped")
        n += 1
    for job in db.session.scalars(db.select(Job).where(Job.worker.is_(None), Job.status == "running")):
        _fail_lost(job, "interrupted by a restart")  # from before jobs had workers
        n += 1
    db.session.commit()
    return n


def workers(within: int = 60) -> list[dict]:
    cutoff = utcnow() - timedelta(seconds=within)
    return [{"id": w.id, "mode": w.mode, "version": w.version, "running": w.running,
             "started_at": w.started_at.isoformat() + "Z", "seen_at": w.seen_at.isoformat() + "Z"}
            for w in db.session.scalars(db.select(Worker).where(Worker.seen_at >= cutoff).order_by(Worker.id))]


def queue_status() -> dict:
    mode = current_app.config.get("LABDEX_WORKER", "inline")
    waiting = db.session.scalar(db.select(db.func.count()).select_from(Job).where(Job.worker.is_(None), Job.status.in_(WAITING))) or 0
    live = workers()
    return {"mode": mode, "workers": live, "waiting": waiting,
            "ok": bool(live) or mode == "inline" or current_app.config.get("LABDEX_JOBS_SYNC", False)}


def start(integration: Integration, action_id: str, *, trigger="manual", params=None, created_by="system",
          replaces: int | None = None, template_id: int | None = None) -> Job:
    spec = ext()["registry"].get(integration.plugin_id)
    action = spec.action(action_id) if spec else None
    if action is None or action["mode"] == "export":
        raise PluginError(f"no runnable action '{action_id}'")
    from .. import demo
    if demo.enabled() and integration.plugin_id not in demo.PLUGINS:
        raise PluginError(f"{integration.plugin_id} doesn't run in the demo")
    params = dict(params or {})
    if action.get("scope") == "host":
        from .. import hostmodules
        from ..models import Host
        from .. import store
        h = db.session.scalar(db.select(Host).where(Host.name == str(params.get("host", ""))))
        if h is None:
            raise PluginError(f"'{action['label']}' runs for one host; give params.host")
        on, why = hostmodules.state(integration, store.host_to_dict(h))
        if not on:
            raise PluginError(f"{integration.name} isn't enabled for {h.name} ({why})")
    if action.get("params"):
        from . import coerce_fields
        given = {k: v for k, v in params.items() if k not in ("host", "limit")}
        clean, errors = coerce_fields(action["params"], given, {}, ext()["vault"])
        if errors:
            raise PluginError("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        params.update(clean)
    with _lock:
        busy = next((j for j in db.session.scalars(db.select(Job).where(
            Job.integration_id == integration.id, Job.action == action_id, Job.status.in_(ACTIVE)))
            if (j.params or {}).get("host") == params.get("host")), None)
        if busy is not None:
            raise PluginError(f"'{action['label']}' is already running (job #{busy.id})")
        if action["mode"] == "plan":
            for old in db.session.scalars(db.select(Job).where(
                    Job.integration_id == integration.id, Job.action == action_id, Job.status.in_(("planned", "approved")))
                    .order_by(Job.id)):
                if (old.params or {}).get("host") != params.get("host"):
                    continue  # plans for other hosts stay
                old.status = "stale"
                old.log = (old.log or "") + "superseded by a newer plan\n"
                if old.approvals and replaces is None:
                    replaces = old.id
        job = Job(integration_id=integration.id, action=action_id, mode=action["mode"], trigger=trigger,
                  params=params, created_by=created_by, inventory_rev=audit.inventory_revision(),
                  phase="plan" if action["mode"] == "plan" else "run", status="queued", replaces=replaces,
                  template_id=template_id)
        if params.get("host"):
            _note_hosts(job, [params["host"]])
        db.session.add(job)
        db.session.commit()
    _submit(current_app._get_current_object(), job.id)
    return job


def approve(job: Job, principal, comment: str = "", skip: list | None = None) -> str:
    """A person approves a plan, perhaps leaving some of its changes out (`skip`: their
    positions in the plan). Returns waiting, scheduled, applying or replanned."""
    from .. import approvals
    why = approvals.why_not(job, principal)
    if why:
        raise PluginError(f"can't approve job #{job.id}: {why}")
    if skip is not None:
        plan = list(job.plan or [])
        try:
            skip = {int(n) for n in skip}
        except (TypeError, ValueError) as exc:
            raise PluginError("skip: positions in the plan (0, 1, …)") from exc
        if any(n < 0 or n >= len(plan) for n in skip):
            raise PluginError("skip: a position that isn't in the plan")
        if skip and any(plan[n].get("op") == "ansible" for n in skip):
            raise PluginError("an Ansible run can't be applied in part")
        if skip and len(skip) == len(plan):
            raise PluginError("every change is unticked: discard the plan instead")
        before = {n for n, c in enumerate(plan) if c.get("skipped")}
        if skip != before:
            job.plan = [dict(c, skipped=n in skip) for n, c in enumerate(plan)]
            names = ", ".join(plan[n].get("name", "?") for n in sorted(skip))
            _append_log(job, f"{principal.label} left out {len(skip)} change(s): {names}" if skip else f"{principal.label} put every change back in")
    approvals.approve(job, principal.label, comment)
    db.session.commit()
    audit.record("approve", "integration", job.integration.name,
                 summary=f"approved {job.action} (job #{job.id}), {len(job.approvals)} of {approvals.needed(job)}"
                 + (f": {comment}" if comment else ""), actor=(principal.label, "user"))
    db.session.commit()
    return approvals.advance(job)


def apply(job: Job, user_label: str) -> Job:
    """Apply now, whatever the policy says (the command line: `labdex action … --apply`)."""
    if job.status not in ("planned", "approved"):
        raise PluginError(f"job #{job.id} is {job.status}, only planned jobs can be applied")
    if job.inventory_rev != audit.inventory_revision():
        job.status = "stale"
        db.session.commit()
        raise PluginError("the inventory changed since this plan was made; plan again")
    apply_now(job, user_label)
    return job


def apply_now(job: Job, by: str) -> str:
    """Queue the apply. A plan older than the inventory is made again instead (approvals
    carry over when the changes are the same). Returns applying or replanned."""
    if job.inventory_rev != audit.inventory_revision():
        job.status = "stale"
        job.log = (job.log or "") + f"[{utcnow().strftime('%H:%M:%S')}] the inventory changed since this plan; planning again\n"
        db.session.commit()
        try:
            new = start(job.integration, job.action, trigger=job.trigger, params=dict(job.params or {}),
                        created_by=job.created_by, replaces=job.id)
        except PluginError as exc:
            _append_log(job, f"couldn't plan again: {exc}")
            return "stale"
        job.result = dict(job.result or {}, replanned_as=new.id)
        db.session.commit()
        return "replanned"
    job.status, job.applied_by, job.phase, job.worker, job.heartbeat_at = "applying", by, "apply", None, None
    job.apply_after = None
    db.session.commit()
    _submit(current_app._get_current_object(), job.id)
    return "applying"


def discard(job: Job, by: str = "", comment: str = "") -> Job:
    if job.status not in ("planned", "approved"):
        raise PluginError(f"job #{job.id} is {job.status}")
    job.status = "discarded"
    job.finished_at = utcnow()
    if by:
        job.result = dict(job.result or {}, discarded_by=by, comment=comment[:500])
    db.session.commit()
    return job


# --------------------------------------------------------------------- hooks
def emit(event: str, payload: dict | None = None) -> list[int]:
    """Called after an inventory change was committed. Starts hooked actions."""
    started = []
    reg = ext()["registry"]
    for integ in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True))):
        spec = reg.get(integ.plugin_id)
        if spec is None or spec.error:
            continue
        for hook in spec.hooks:
            if (spec.action(hook["action"]) or {}).get("scope") == "host":
                continue
            if any(fnmatchcase(event, pat) for pat in hook.get("events", [])):
                try:
                    started.append(start(integ, hook["action"], trigger="hook", created_by=f"hook {event}").id)
                except PluginError:
                    # already running and may have read the old inventory: plan again when it's done
                    st = dict(integ.state or {})
                    st["rerun"] = sorted(set(st.get("rerun", [])) | {hook["action"]})
                    integ.state = st
                    db.session.commit()
    return started


def _rerun_if_pending(job_id: int) -> None:
    """A hook fired while this job ran: start the action once more so the plan is current."""
    job = db.session.get(Job, job_id)
    integ = job.integration
    if integ is None or job.action not in (integ.state or {}).get("rerun", []):
        return
    st = dict(integ.state)
    st["rerun"] = [a for a in st["rerun"] if a != job.action]
    integ.state = st
    db.session.commit()
    try:
        start(integ, job.action, trigger="hook", created_by="hook (changes during the last run)")
    except PluginError:
        pass


# ----------------------------------------------------------------- scheduler
def _deliver(app: Flask) -> None:
    from .. import notify
    try:
        notify.deliver()
    except Exception:
        db.session.rollback()
        app.logger.exception("sending notifications failed")


def _due(app: Flask) -> None:
    """Approved plans whose delay passed and whose change window is open."""
    from .. import approvals
    with app.app_context():
        try:
            from .. import demo
            if demo.maybe_reset():
                return
            approvals.due()
            from .. import health, notify
            notify.prune()
            health.reconcile()
        except Exception:
            db.session.rollback()
            app.logger.exception("applying approved plans failed")
        finally:
            db.session.remove()


def _tick(app: Flask) -> None:
    with app.app_context():
        try:
            from .. import templates
            try:
                templates.due()
            except Exception:
                db.session.rollback()
                app.logger.exception("starting scheduled templates failed")
            try:
                from . import updates
                updates.check()  # every few hours; announces updates, installs official ones if asked to
            except Exception:
                db.session.rollback()
                app.logger.exception("checking for plugin updates failed")
            reg = ext(app)["registry"]
            now = utcnow()
            for integ in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True))):
                spec = reg.get(integ.plugin_id)
                if spec is None or spec.error:
                    continue
                for a in spec.actions:
                    if not a.get("schedulable") or a.get("scope") == "host":
                        continue
                    minutes = int((integ.settings or {}).get(f"schedule_{a['id']}") or 0)
                    if minutes <= 0:
                        continue
                    last = (integ.state or {}).get("scheduled", {}).get(a["id"])
                    if last and now - _parse(last) < timedelta(minutes=minutes):
                        continue
                    state = dict(integ.state or {})
                    state.setdefault("scheduled", {})[a["id"]] = now.isoformat()
                    integ.state = state
                    db.session.commit()
                    try:
                        start(integ, a["id"], trigger="schedule", created_by="scheduler")
                    except PluginError:
                        pass
        finally:
            db.session.remove()


def _parse(s: str):
    from datetime import datetime
    return datetime.fromisoformat(s)


def init(app: Flask) -> None:
    """LABDEX_WORKER=inline (default): this process runs jobs and the scheduler.
    LABDEX_WORKER=external: it only queues them; `labdex worker` runs them."""
    mode = app.config.get("LABDEX_WORKER", "inline")
    app.extensions["labdex"]["dispatcher"] = None
    if mode != "inline" or app.config.get("LABDEX_JOBS_SYNC") or not app.config.get("LABDEX_DISPATCHER", True):
        return
    started = threading.Lock()

    # Started by the first request, so CLI commands (which build the app too) never pick up jobs.
    @app.before_request
    def _start_dispatcher():
        if app.extensions["labdex"]["dispatcher"] is not None:
            return
        with started:
            if app.extensions["labdex"]["dispatcher"] is None:
                d = Dispatcher(app, "inline", scheduler=bool(app.config.get("LABDEX_SCHEDULER")))
                app.extensions["labdex"]["dispatcher"] = d
                d.start_thread()


def run_worker(app: Flask, slots: int = 2, max_seconds: float | None = None) -> None:
    """The `labdex worker` process."""
    d = Dispatcher(app, "external", slots=slots, scheduler=bool(app.config.get("LABDEX_SCHEDULER", True)))
    app.extensions["labdex"]["dispatcher"] = d
    d.loop(max_seconds=max_seconds)
    d.pool.shutdown(wait=True)
