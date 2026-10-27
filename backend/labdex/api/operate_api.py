"""Approval policies and change windows. Everyone signed in reads them; administrators edit."""
from __future__ import annotations

from flask import g, jsonify

from .. import approvals, audit, rbac, store
from ..models import ApprovalPolicy, ChangeWindow, db
from . import body, bp, err

POLICY_FIELDS = ("name", "enabled", "integrations", "zones", "triggers", "approvals", "approvers", "self_approval",
                 "delay_minutes", "window_id", "description")
WINDOW_FIELDS = ("name", "days", "start", "end", "timezone", "description")


def _check(errors):
    if errors:
        raise store.ValidationError(errors)


# ------------------------------------------------------------------ policies
@bp.get("/approval-policies")
def list_policies():
    rows = db.session.scalars(db.select(ApprovalPolicy).order_by(ApprovalPolicy.position, ApprovalPolicy.id))
    return jsonify({"policies": [approvals.policy_dict(p) for p in rows], "default": approvals.DEFAULT,
                    "can_edit": bool(g.principal.is_admin)})


def _apply_policy(p: ApprovalPolicy, d: dict) -> None:
    d = {k: d[k] for k in POLICY_FIELDS if k in d}
    merged = approvals.policy_dict(p) | d if p.id else {"integrations": ["*"], "zones": ["*"], "triggers": list(approvals.TRIGGERS),
                                                         "approvals": 1, "approvers": [], "delay_minutes": 0} | d
    errs = approvals.validate_policy(merged)
    name = str(merged.get("name", "")).strip()
    other = db.session.scalar(db.select(ApprovalPolicy).where(ApprovalPolicy.name == name))
    if other is not None and other.id != p.id:
        errs.append({"field": "name", "message": "a policy with this name exists"})
    _check(errs)
    p.name = name
    p.enabled = bool(merged.get("enabled", True))
    p.integrations = [x.strip() for x in merged["integrations"]] or ["*"]
    p.zones = [x.strip() for x in merged["zones"]] or ["*"]
    p.triggers = list(merged["triggers"])
    p.approvals = int(merged["approvals"])
    p.approvers = [x.strip() if x.startswith("@") else x.strip().lower() for x in merged["approvers"]]
    p.self_approval = bool(merged.get("self_approval", True))
    p.delay_minutes = int(merged.get("delay_minutes") or 0)
    p.window_id = int(merged["window_id"]) if merged.get("window_id") else None
    p.description = str(merged.get("description") or "")[:200]


@bp.post("/approval-policies")
def create_policy():
    rbac.require_admin()
    p = ApprovalPolicy()
    _apply_policy(p, body())
    p.position = (db.session.scalar(db.select(db.func.max(ApprovalPolicy.position))) or 0) + 1
    db.session.add(p)
    db.session.flush()
    audit.record("create", "approval_policy", p.name, after=approvals.policy_dict(p))
    db.session.commit()
    return jsonify(approvals.policy_dict(p)), 201


@bp.put("/approval-policies/<int:pid>")
def update_policy(pid):
    rbac.require_admin()
    p = db.session.get(ApprovalPolicy, pid) or _404("policy")
    before = approvals.policy_dict(p)
    _apply_policy(p, body())
    db.session.flush()
    audit.record("update", "approval_policy", p.name, before=before, after=approvals.policy_dict(p))
    db.session.commit()
    return jsonify(approvals.policy_dict(p))


@bp.put("/approval-policies/order")
def order_policies():
    rbac.require_admin()
    ids = body().get("ids") or []
    rows = {p.id: p for p in db.session.scalars(db.select(ApprovalPolicy))}
    if sorted(ids) != sorted(rows):
        return err(422, "give every policy id once")
    for n, i in enumerate(ids):
        rows[i].position = n + 1
    audit.record("update", "approval_policy", "order", summary="reordered approval policies")
    db.session.commit()
    return list_policies()


@bp.delete("/approval-policies/<int:pid>")
def delete_policy(pid):
    rbac.require_admin()
    p = db.session.get(ApprovalPolicy, pid) or _404("policy")
    audit.record("delete", "approval_policy", p.name, before=approvals.policy_dict(p))
    db.session.delete(p)
    db.session.commit()
    return "", 204


# ------------------------------------------------------------------ windows
def _window_out(w: ChangeWindow) -> dict:
    d = approvals.window_dict(w)
    d["open_now"] = approvals.window_open(d)
    d["next_open"] = approvals.next_open(d).isoformat() + "Z"
    d["policies"] = [p.name for p in db.session.scalars(db.select(ApprovalPolicy).where(ApprovalPolicy.window_id == w.id))]
    return d


@bp.get("/change-windows")
def list_windows():
    return jsonify([_window_out(w) for w in db.session.scalars(db.select(ChangeWindow).order_by(ChangeWindow.name))])


def _apply_window(w: ChangeWindow, d: dict) -> None:
    d = {k: d[k] for k in WINDOW_FIELDS if k in d}
    merged = (approvals.window_dict(w) if w.id else {"days": approvals.DAYS, "start": "20:00", "end": "22:00", "timezone": "UTC"}) | d
    errs = approvals.validate_window(merged)
    name = str(merged.get("name", "")).strip()
    other = db.session.scalar(db.select(ChangeWindow).where(ChangeWindow.name == name))
    if other is not None and other.id != w.id:
        errs.append({"field": "name", "message": "a window with this name exists"})
    _check(errs)
    w.name, w.start, w.end = name, merged["start"], merged["end"]
    w.days = [x for x in approvals.DAYS if x in merged["days"]]
    w.timezone = str(merged.get("timezone") or "UTC")
    w.description = str(merged.get("description") or "")[:200]


@bp.post("/change-windows")
def create_window():
    rbac.require_admin()
    w = ChangeWindow()
    _apply_window(w, body())
    db.session.add(w)
    db.session.flush()
    audit.record("create", "change_window", w.name, after=approvals.window_dict(w))
    db.session.commit()
    return jsonify(_window_out(w)), 201


@bp.put("/change-windows/<int:wid>")
def update_window(wid):
    rbac.require_admin()
    w = db.session.get(ChangeWindow, wid) or _404("change window")
    before = approvals.window_dict(w)
    _apply_window(w, body())
    audit.record("update", "change_window", w.name, before=before, after=approvals.window_dict(w))
    db.session.commit()
    return jsonify(_window_out(w))


@bp.delete("/change-windows/<int:wid>")
def delete_window(wid):
    rbac.require_admin()
    w = db.session.get(ChangeWindow, wid) or _404("change window")
    audit.record("delete", "change_window", w.name, before=approvals.window_dict(w))
    db.session.delete(w)
    db.session.commit()
    return "", 204


def _404(what):
    from flask import abort
    abort(404, description=f"no such {what}")


# ------------------------------------------------------------ notifications
def _channel(cid: int):
    from ..models import NotificationChannel
    rbac.require_admin()
    return db.session.get(NotificationChannel, cid) or _404("channel")


@bp.get("/notification-channels")
def list_channels():
    from .. import notify
    from ..models import NotificationChannel
    rbac.require_admin()
    return jsonify({"channels": [notify.channel_dict(c) for c in db.session.scalars(db.select(NotificationChannel).order_by(NotificationChannel.name))],
                    "kinds": {k: {"label": v["label"], "fields": v["fields"]} for k, v in notify.KINDS.items()},
                    "events": [{"event": e, "label": label} for e, label in notify.EVENTS]})


def _apply_channel(ch, d: dict) -> None:
    from fnmatch import fnmatchcase
    from flask import current_app
    from .. import notify
    from ..models import NotificationChannel
    from ..plugins import coerce_fields
    errs = []
    name = str(d.get("name", ch.name or "")).strip()
    if not name:
        errs.append({"field": "name", "message": "required"})
    other = db.session.scalar(db.select(NotificationChannel).where(NotificationChannel.name == name))
    if other is not None and other.id != ch.id:
        errs.append({"field": "name", "message": "a channel with this name exists"})
    kind = d.get("kind", ch.kind)
    if kind not in notify.KINDS:
        errs.append({"field": "kind", "message": "one of " + ", ".join(notify.KINDS)})
        _check(errs)
    if ch.kind and kind != ch.kind:
        errs.append({"field": "kind", "message": "can't change; make a new channel"})
    events = d.get("events", ch.events if ch.id else [e for e, _ in notify.EVENTS[:4]])
    known = [e for e, _ in notify.EVENTS] + ["test"]
    if not isinstance(events, list) or any(not isinstance(e, str) or not e.strip() for e in events):
        errs.append({"field": "events", "message": "a list of events"})
    elif not any(fnmatchcase(k, e) for e in events for k in known) and not any(e.startswith("plugin.") for e in events):
        errs.append({"field": "events", "message": "matches no event"})
    integs = d.get("integrations", ch.integrations if ch.id else ["*"])
    if not isinstance(integs, list):
        errs.append({"field": "integrations", "message": "a list of names"})
    clean, ferrs = coerce_fields(notify.KINDS[kind]["fields"], d.get("settings") or {}, ch.settings or {},
                                 current_app.extensions["labdex"]["vault"])
    errs += [{"field": f"settings.{e['field']}", "message": e["message"]} for e in ferrs]
    for k in ("server", "homeserver", "url"):
        if clean.get(k) and not str(clean[k]).startswith(("https://", "http://")):
            errs.append({"field": f"settings.{k}", "message": "starts with https://"})
    _check(errs)
    ch.name, ch.kind, ch.settings = name, kind, clean
    ch.enabled = bool(d.get("enabled", ch.enabled if ch.id else True))
    ch.events = [e.strip() for e in events]
    ch.integrations = [x.strip() for x in integs if str(x).strip()] or ["*"]


@bp.post("/notification-channels")
def create_channel():
    from .. import notify
    from ..models import NotificationChannel
    rbac.require_admin()
    ch = NotificationChannel(kind="", events=[], integrations=["*"], settings={})
    ch.kind = ""
    _apply_channel(ch, body())
    db.session.add(ch)
    db.session.flush()
    audit.record("create", "notification_channel", ch.name, summary=f"added {ch.kind} channel {ch.name}")
    db.session.commit()
    return jsonify(notify.channel_dict(ch)), 201


@bp.put("/notification-channels/<int:cid>")
def update_channel(cid):
    from .. import notify
    ch = _channel(cid)
    _apply_channel(ch, body())
    audit.record("update", "notification_channel", ch.name, summary=f"changed channel {ch.name}")
    db.session.commit()
    return jsonify(notify.channel_dict(ch))


@bp.delete("/notification-channels/<int:cid>")
def delete_channel(cid):
    ch = _channel(cid)
    audit.record("delete", "notification_channel", ch.name, summary=f"removed channel {ch.name}")
    db.session.delete(ch)
    db.session.commit()
    return "", 204


@bp.post("/notification-channels/<int:cid>/test")
def test_channel(cid):
    """Send a test right away and say how it went."""
    from .. import notify
    from ..models import Notification
    ch = _channel(cid)
    n = Notification(channel=ch, event="test", title="labdex test notification",
                     body=f"If you read this, channel '{ch.name}' works.", link=notify._link("/admin/notifications"),
                     data={}, attempts=1, created_at=notify.utcnow(), status="sending")
    try:
        notify.send(n)
        n.status, n.sent_at = "sent", notify.utcnow()
    except notify.SendError as exc:
        n.status, n.error = "failed", str(exc)[:1000]
    db.session.add(n)
    db.session.commit()
    return jsonify(notify.notification_dict(n)), 200 if n.status == "sent" else 502


@bp.get("/notifications")
def list_notifications():
    from flask import request
    from .. import notify
    from ..models import Notification
    rbac.require_admin()
    q = db.select(Notification).order_by(Notification.id.desc()).limit(min(int(request.args.get("limit", 50)), 200))
    if request.args.get("status"):
        q = q.where(Notification.status == request.args["status"])
    return jsonify([notify.notification_dict(n) for n in db.session.scalars(q)])


# ------------------------------------------------------------ job templates
def _tpl_check(integ_name: str, host: str, scheduled: bool) -> None:
    """Running a template needs integration:apply (and host apply for a host action); saving one
    that runs on a schedule, unattended, needs integration:admin."""
    rbac.require("integration", "admin" if scheduled else "apply", rbac.named_facts(integ_name))
    if host:
        from .. import store
        from ..models import Host
        h = db.session.scalar(db.select(Host).where(Host.name == host))
        if h is not None:
            rbac.require("host", "apply", rbac.host_facts(store.host_access(h)))


def _tpl(tid: int):
    from ..models import JobTemplate
    t = db.session.get(JobTemplate, tid)
    if t is None or not g.principal.can("integration", "view", rbac.named_facts(t.integration.name)):
        _404("template")
    return t


def _registry():
    from flask import current_app
    return current_app.extensions["labdex"]["registry"], current_app.extensions["labdex"]["vault"]


@bp.get("/job-templates")
def list_templates():
    from flask import request
    from .. import templates
    from ..models import Integration, JobTemplate
    reg, _ = _registry()
    q = db.select(JobTemplate).order_by(JobTemplate.name)
    if request.args.get("integration"):
        q = q.join(Integration).where(Integration.name == request.args["integration"])
    out = []
    for t in db.session.scalars(q):
        if g.principal.can("integration", "view", rbac.named_facts(t.integration.name)):
            d = templates.to_dict(t, reg.get(t.integration.plugin_id))
            d["can_run"] = g.principal.can("integration", "apply", rbac.named_facts(t.integration.name))
            out.append(d)
    return jsonify(out)


@bp.post("/job-templates")
def create_template():
    from .. import templates
    from ..models import JobTemplate
    reg, vault = _registry()
    d = body()
    _tpl_check(str(d.get("integration", "")), str(d.get("host", "")), bool(d.get("schedule")))
    t = JobTemplate(created_by=g.principal.label)
    _check(templates.apply(t, d, reg, vault))
    db.session.add(t)
    db.session.flush()
    audit.record("create", "job_template", t.name, after=templates.to_dict(t))
    db.session.commit()
    return jsonify(templates.to_dict(t, reg.get(t.integration.plugin_id))), 201


@bp.put("/job-templates/<int:tid>")
def update_template(tid):
    from .. import templates
    reg, vault = _registry()
    t = _tpl(tid)
    d = body()
    _tpl_check(t.integration.name, t.host, bool(t.schedule))
    before = templates.to_dict(t)
    _check(templates.apply(t, d, reg, vault))
    _tpl_check(t.integration.name, t.host, bool(t.schedule))
    audit.record("update", "job_template", t.name, before=before, after=templates.to_dict(t))
    db.session.commit()
    return jsonify(templates.to_dict(t, reg.get(t.integration.plugin_id)))


@bp.delete("/job-templates/<int:tid>")
def delete_template(tid):
    from .. import templates
    t = _tpl(tid)
    _tpl_check(t.integration.name, t.host, bool(t.schedule))
    audit.record("delete", "job_template", t.name, before=templates.to_dict(t))
    db.session.delete(t)
    db.session.commit()
    return "", 204


@bp.post("/job-templates/<int:tid>/run")
def run_template(tid):
    from .. import templates
    from ..models import Job
    from .plugins_api import job_dict
    t = _tpl(tid)
    _tpl_check(t.integration.name, t.host, False)
    if not t.integration.enabled:
        return err(409, "enable the integration first")
    job = templates.run(t, trigger="manual", created_by=g.principal.label)
    db.session.expire_all()
    return jsonify(job_dict(db.session.get(Job, job.id), full=True)), 202
