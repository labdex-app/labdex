"""The start page: what's there, what waits for you, what fails, what changed."""
from __future__ import annotations

from datetime import timedelta

from flask import current_app, g, jsonify

from .. import approvals, audit, discovery, lint, rbac, store
from ..models import AuditEvent, ChangeWindow, Job, JobTemplate, Network, Notification, db, utcnow
from ..plugins import jobs
from . import bp
from .plugins_api import job_dict


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


@bp.get("/dashboard")
def dashboard():
    p = g.principal
    now = utcnow()
    from .. import health
    hst = health.states()
    live = {k: v["live"] for k, v in hst.items()}

    # ------------------------------------------------------------- inventory
    hosts = [e for e in store.host_index() if p.can("host", "view", e["facts"])]
    by_status: dict[str, int] = {}
    up = down = unmonitored = 0
    zones: dict[str, int] = {}
    for e in hosts:
        d = e["host"]
        by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        zones[d["zone"]] = zones.get(d["zone"], 0) + 1
        st = live.get(d["name"])
        if d["status"] == "active" and d.get("kind") != "vip":
            up += st == "up"
            down += st == "down"
            unmonitored += (hst.get(d["name"]) or {}).get("by") != "sources"
    nets = [n for n in db.session.scalars(db.select(Network)) if p.can("network", "view", rbac.network_facts(store.network_to_dict(n)))]
    services = sum(len(e["host"]["services"]) for e in hosts)
    from .inventory_api import visible_issues
    issues = visible_issues(p)
    counts = {"hosts": len(hosts), "by_status": by_status, "up": up, "down": down, "unmonitored": unmonitored,
              "monitoring": any(x["by"] == "sources" for x in hst.values()), "networks": len(nets), "services": services,
              "zones": dict(sorted(zones.items())), "lint": lint.summary(issues)}
    if p.can_any("discovery", "view"):
        try:
            s = discovery.summary()
            counts["discovery"] = {"unknown_hosts": s["unknown_hosts"], "undocumented": s["undocumented"], "missing": s["missing"],
                                   "macs": s["macs"]}
        except Exception:  # discovery is optional; never break the start page
            current_app.logger.exception("discovery summary failed")

    # ---------------------------------------------------------- plans waiting
    def can_view(j: Job) -> bool:
        return bool(j.integration) and p.can("integration", "view", rbac.named_facts(j.integration.name))

    waiting, scheduled = [], []
    for j in db.session.scalars(db.select(Job).where(Job.status.in_(("planned", "approved"))).order_by(Job.id.desc()).limit(200)):
        if not can_view(j):
            continue
        d = job_dict(j)
        if j.status == "planned":
            can_apply = p.can("integration", "apply", rbac.named_facts(j.integration.name))
            d["can_approve"] = can_apply and not approvals.why_not(j, p)
            waiting.append(d)
        else:
            scheduled.append(d)
    waiting.sort(key=lambda d: (not d["can_approve"], -d["id"]))
    scheduled.sort(key=lambda d: d["apply_after"] or "")

    # ---------------------------------------------------------------- failing
    # the newest run of each integration action (and host) that failed in the last week, unless a later one succeeded
    since = now - timedelta(days=7)
    latest: dict[tuple, Job] = {}
    for j in db.session.scalars(db.select(Job).where(Job.created_at >= since).order_by(Job.id)):
        if j.status in ("queued", "running", "applying", "planned", "approved", "stale", "discarded"):
            continue
        latest[(j.integration_id, j.action, (j.params or {}).get("host"))] = j
    failing = [job_dict(j) for j in sorted(latest.values(), key=lambda j: -j.id) if j.status == "failed" and can_view(j)][:10]
    running = [job_dict(j) for j in db.session.scalars(db.select(Job).where(Job.status.in_(jobs.ACTIVE)).order_by(Job.id.desc()).limit(10))
               if can_view(j)]

    # ---------------------------------------------------------------- upcoming
    upcoming = []
    for t in db.session.scalars(db.select(JobTemplate).where(JobTemplate.next_run_at.is_not(None)).order_by(JobTemplate.next_run_at).limit(20)):
        if p.can("integration", "view", rbac.named_facts(t.integration.name)):
            upcoming.append({"kind": "template", "name": t.name, "integration": t.integration.name, "at": _iso(t.next_run_at),
                             "host": t.host or None})
    windows = []
    for w in db.session.scalars(db.select(ChangeWindow).order_by(ChangeWindow.name)):
        wd = approvals.window_dict(w)
        windows.append({"name": w.name, "open": approvals.window_open(wd, now), "next_open": _iso(approvals.next_open(wd, now))})

    # --------------------------------------------------------- recent changes
    changes = []
    for ev in db.session.scalars(db.select(AuditEvent).where(AuditEvent.action.not_in(("login", "logout")))
                                 .order_by(AuditEvent.id.desc()).limit(150)):
        if audit.visible(ev):
            changes.append(audit.to_dict(ev))
            if len(changes) >= 8:
                break

    # ------------------------------------------------------------------ health
    health = {"jobs": jobs.queue_status()}
    if p.is_admin:
        health["notifications_failed"] = db.session.scalar(db.select(db.func.count()).select_from(Notification).where(
            Notification.status == "failed", Notification.created_at >= since)) or 0
    if not p.is_admin:
        health["jobs"]["workers"] = [{"mode": w["mode"], "running": w["running"]} for w in health["jobs"]["workers"]]

    return jsonify({"counts": counts, "waiting": waiting[:15], "scheduled": scheduled[:10], "failing": failing,
                    "running": running, "upcoming": upcoming[:8], "windows": windows, "changes": changes, "health": health,
                    "lint_top": [i for i in issues if i["severity"] == "error"][:5]})


@bp.get("/widgets")
def dashboard_widgets():
    """The dashboard widgets of every enabled plugin instance you can see."""
    from .. import widgets
    p = g.principal
    items = [(i, s, w) for i, s, w in widgets.declared("dashboard") if p.can("integration", "view", rbac.named_facts(i.name))]
    return jsonify(widgets.render(items))


@bp.get("/hosts/<name>/widgets")
def host_widgets(name):
    """Widgets of the plugin instances that are on for this host."""
    from .. import hostmodules, widgets
    from ..models import Host
    p = g.principal
    h = db.session.scalar(db.select(Host).where(Host.name == name))
    d = store.host_to_dict(h) if h else None
    if h is None or not p.can("host", "view", rbac.host_facts(d)):
        from flask import abort
        abort(404, description=f"no host '{name}'")
    items = [(i, s, w) for i, s, w in widgets.declared("host")
             if p.can("integration", "view", rbac.named_facts(i.name)) and hostmodules.state(i, d)[0]]
    return jsonify(widgets.render(items, host=d))
