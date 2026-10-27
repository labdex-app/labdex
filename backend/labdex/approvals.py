"""Approvals and change windows for plans.

A plan (a job in status `planned`) is matched against the approval policies when it
is made. The policy says how many approvals it needs (0 = none), who may give them,
whether the person who started it may approve it, a delay after planning, and a
change window. Once approved, a plan applies right away, or waits as `approved`
until its delay has passed and its window is open; the scheduler applies it then.

When the inventory changed in between, the plan is made again. If the new plan has
the same changes (same fingerprint), the approvals carry over, so a scheduled plan
approved in the afternoon still applies in the evening window.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, UTC
from fnmatch import fnmatchcase
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ApprovalPolicy, ChangeWindow, Host, Job, User, db, utcnow

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TRIGGERS = ("manual", "hook", "schedule")
DEFAULT = {"id": None, "name": "default", "approvals": 1, "approvers": [], "self_approval": True,
           "delay_minutes": 0, "window": None}


# ------------------------------------------------------------------ windows
def _tz(name: str):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _hm(s: str) -> tuple[int, int]:
    h, m = (s or "00:00").split(":")
    return int(h), int(m)


def window_dict(w: ChangeWindow | None) -> dict | None:
    if w is None:
        return None
    return {"id": w.id, "name": w.name, "days": list(w.days or []), "start": w.start, "end": w.end,
            "timezone": w.timezone, "description": w.description}


def _spans(win: dict, around: datetime):
    """Open intervals (UTC, naive) of a window for the days around `around` (UTC naive)."""
    tz = _tz(win.get("timezone"))
    local = around.replace(tzinfo=UTC).astimezone(tz)
    sh, sm = _hm(win["start"])
    eh, em = _hm(win["end"])
    days = set(win.get("days") or DAYS)
    for offset in range(-1, 9):
        day = (local + timedelta(days=offset)).date()
        if DAYS[day.weekday()] not in days:
            continue
        start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
        end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
        if end <= start:
            end += timedelta(days=1)
        yield (start.astimezone(UTC).replace(tzinfo=None), end.astimezone(UTC).replace(tzinfo=None))


def window_open(win: dict | None, at: datetime | None = None) -> bool:
    if not win:
        return True
    at = at or utcnow()
    return any(s <= at < e for s, e in _spans(win, at))


def next_open(win: dict | None, after: datetime | None = None) -> datetime:
    """The first moment at or after `after` when the window is open."""
    after = after or utcnow()
    if not win:
        return after
    best = None
    for s, e in _spans(win, after):
        if s <= after < e:
            return after
        if s > after and (best is None or s < best):
            best = s
    return best or after + timedelta(days=7)


def validate_window(d: dict) -> list[dict]:
    errs = []
    if not str(d.get("name", "")).strip():
        errs.append({"field": "name", "message": "required"})
    days = d.get("days")
    if not isinstance(days, list) or not days or any(x not in DAYS for x in days):
        errs.append({"field": "days", "message": "pick at least one of " + ", ".join(DAYS)})
    for k in ("start", "end"):
        v = str(d.get(k, ""))
        try:
            h, m = _hm(v)
            if not (0 <= h < 24 and 0 <= m < 60) or len(v) != 5:
                raise ValueError
        except ValueError:
            errs.append({"field": k, "message": "HH:MM, e.g. 20:00"})
    try:
        ZoneInfo(str(d.get("timezone") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        errs.append({"field": "timezone", "message": "unknown time zone (e.g. Europe/Berlin)"})
    return errs


# ------------------------------------------------------------------ policies
def policy_dict(p: ApprovalPolicy) -> dict:
    return {"id": p.id, "name": p.name, "position": p.position, "enabled": p.enabled,
            "integrations": list(p.integrations or ["*"]), "zones": list(p.zones or ["*"]),
            "triggers": list(p.triggers or TRIGGERS), "approvals": p.approvals, "approvers": list(p.approvers or []),
            "self_approval": p.self_approval, "delay_minutes": p.delay_minutes, "window_id": p.window_id,
            "window": p.window.name if p.window else None, "description": p.description}


def validate_policy(d: dict) -> list[dict]:
    errs = []
    if not str(d.get("name", "")).strip():
        errs.append({"field": "name", "message": "required"})
    for k in ("integrations", "zones", "approvers"):
        if not isinstance(d.get(k, []), list) or any(not isinstance(x, str) or not x.strip() for x in d.get(k, [])):
            errs.append({"field": k, "message": "a list of names"})
    tr = d.get("triggers", list(TRIGGERS))
    if not isinstance(tr, list) or not tr or any(t not in TRIGGERS for t in tr):
        errs.append({"field": "triggers", "message": "pick at least one of manual, hook, schedule"})
    for k, hi in (("approvals", 5), ("delay_minutes", 7 * 24 * 60)):
        try:
            if not 0 <= int(d.get(k, 0)) <= hi:
                raise ValueError
        except (TypeError, ValueError):
            errs.append({"field": k, "message": f"0 to {hi}"})
    wid = d.get("window_id")
    if wid not in (None, "", 0) and db.session.get(ChangeWindow, int(wid)) is None:
        errs.append({"field": "window_id", "message": "no such change window"})
    for a in d.get("approvers", []) if isinstance(d.get("approvers"), list) else []:
        if a.startswith("@"):
            from .models import Group
            if db.session.scalar(db.select(Group).where(Group.name == a[1:])) is None:
                errs.append({"field": "approvers", "message": f"no group '{a[1:]}'"})
        elif db.session.scalar(db.select(User).where(User.username == a.lower())) is None:
            errs.append({"field": "approvers", "message": f"no user '{a}'"})
    return errs


def _zones_of(job: Job) -> list[str]:
    names = [h.host for h in job.hosts]
    if not names:
        return []
    return sorted({z for z in db.session.scalars(db.select(Host.zone).where(Host.name.in_(names))) if z})


def match(job: Job) -> dict:
    """The policy for a job, as a snapshot stored on the job."""
    integ = job.integration.name if job.integration else ""
    zones = _zones_of(job)
    for p in db.session.scalars(db.select(ApprovalPolicy).where(ApprovalPolicy.enabled.is_(True))
                                .order_by(ApprovalPolicy.position, ApprovalPolicy.id)):
        if not any(fnmatchcase(integ, g) for g in p.integrations or ["*"]):
            continue
        if (job.trigger if job.trigger in TRIGGERS else "manual") not in (p.triggers or TRIGGERS):
            continue
        globs = p.zones or ["*"]
        if zones:
            if not all(any(fnmatchcase(z, g) for g in globs) for z in zones):
                continue
        elif "*" not in globs:
            continue  # a job for no host in particular only matches policies for every zone
        return {"id": p.id, "name": p.name, "approvals": p.approvals, "approvers": list(p.approvers or []),
                "self_approval": p.self_approval, "delay_minutes": p.delay_minutes, "window": window_dict(p.window)}
    return dict(DEFAULT)


def fingerprint(plan: list) -> str:
    return hashlib.sha256(json.dumps(plan or [], sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------ deciding
def needed(job: Job) -> int:
    return int((job.policy or DEFAULT).get("approvals", 1))


def _in_approvers(principal, approvers: list[str]) -> bool:
    if not approvers:
        return True
    user = principal.user
    if user is None:
        return False
    if user.username in [a.lower() for a in approvers if not a.startswith("@")]:
        return True
    groups = {g.name for g in user.groups}
    return any(a[1:] in groups for a in approvers if a.startswith("@"))


def why_not(job: Job, principal) -> str:
    """'' when this principal may approve the job now, else the reason."""
    if job.status != "planned":
        return f"the job is {job.status}"
    pol = job.policy or DEFAULT
    label = principal.label
    if any(a.get("by") == label for a in job.approvals or []):
        return "you approved it already"
    if not _in_approvers(principal, pol.get("approvers") or []):
        return "approvers: " + ", ".join(pol["approvers"])
    if not pol.get("self_approval", True) and job.created_by == label:
        return "you started this plan; someone else approves it"
    return ""


def earliest(job: Job) -> datetime:
    pol = job.policy or DEFAULT
    base = (job.finished_at or job.created_at or utcnow()) + timedelta(minutes=int(pol.get("delay_minutes") or 0))
    return next_open(pol.get("window"), max(base, utcnow()))


def approve(job: Job, by: str, comment: str = "") -> None:
    job.approvals = list(job.approvals or []) + [{"by": by, "at": utcnow().isoformat() + "Z", "comment": comment[:500]}]


def ready(job: Job) -> bool:
    return len(job.approvals or []) >= needed(job)


def advance(job: Job) -> str:
    """After a plan was made or approved: apply now, wait for the window, or keep waiting
    for approvals. Returns what happened: waiting, scheduled, applying, replanned."""
    from .plugins import jobs
    if job.status not in ("planned", "approved") or not ready(job):
        return "waiting"
    when = earliest(job)
    if when > utcnow():
        job.status, job.apply_after = "approved", when
        db.session.commit()
        return "scheduled"
    by = (job.approvals[-1]["by"] if job.approvals else "policy")
    return jobs.apply_now(job, by)


def due() -> list[int]:
    """Approved plans whose time has come (the scheduler calls this)."""
    from .plugins import jobs
    started = []
    now = utcnow()
    for job in list(db.session.scalars(db.select(Job).where(Job.status == "approved", Job.apply_after <= now))):
        pol = job.policy or DEFAULT
        if not window_open(pol.get("window"), now):
            job.apply_after = next_open(pol.get("window"), now)
            db.session.commit()
            continue
        by = job.approvals[-1]["by"] if job.approvals else "policy"
        jobs.apply_now(job, by)
        started.append(job.id)
    return started


def carry_over(job: Job) -> None:
    """A re-plan with the same changes keeps the approvals of the plan it replaces."""
    if not job.replaces:
        return
    old = db.session.get(Job, job.replaces)
    if old is None or not old.approvals or old.fingerprint != job.fingerprint or not job.fingerprint:
        return
    if (old.policy or {}).get("id") != (job.policy or {}).get("id"):
        return
    job.approvals = list(old.approvals)
    skipped = [n for n, c in enumerate(old.plan or []) if c.get("skipped")]
    if skipped and len(old.plan or []) == len(job.plan or []):  # what was unticked stays unticked
        job.plan = [dict(c, skipped=n in skipped) for n, c in enumerate(job.plan)]
