"""Notifications: events go to channels (ntfy, Matrix, email, webhooks) through an outbox.

`emit(event, title, body, link, data)` writes one outbox row per enabled channel whose
event patterns match; the dispatcher (web server or `labdex worker`) sends them and
retries failures a few times. Events labdex itself sends:

    plan.waiting    a plan needs approval
    plan.applied    a plan was applied
    job.failed      an action failed (a plan, a run or an apply)
    backup.failed   a backup failed
    host.down       a host stopped answering (sent by status integrations)
    host.up         … and came back
    plugins.update  a plugin update is available (once per version)
    plugins.updated / plugins.update_failed / plugins.withdrawn   see plugins/updates.py
    test            the "Send a test" button

Plugins emit their own with `ctx.notify(event, title, body)`; those are named
`plugin.<plugin id>.<event>` unless they are host.down or host.up.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from fnmatch import fnmatchcase
from urllib.parse import quote

import requests
from flask import current_app

from .models import Notification, NotificationChannel, db, utcnow

EVENTS = [
    ("plan.waiting", "A plan needs approval"),
    ("plan.applied", "A plan was applied"),
    ("job.failed", "An action failed"),
    ("backup.failed", "A backup failed"),
    ("host.down", "A host stopped answering"),
    ("host.up", "A host answers again"),
    ("plugins.update", "A plugin update is available"),
    ("plugins.updated", "A plugin updated itself (official updates)"),
    ("plugins.update_failed", "An automatic plugin update failed"),
    ("plugins.withdrawn", "An installed plugin version was withdrawn"),
]
RETRY = [60, 300, 900, 3600]  # seconds before attempts 2..5
TIMEOUT = 10

KINDS = {
    "ntfy": {"label": "ntfy", "fields": [
        {"key": "server", "label": "Server", "type": "string", "default": "https://ntfy.sh", "required": True},
        {"key": "topic", "label": "Topic", "type": "string", "required": True},
        {"key": "token", "label": "Access token", "type": "secret", "help": "For protected topics"},
        {"key": "priority", "label": "Priority", "type": "select", "default": "default",
         "options": ["min", "low", "default", "high", "max"]},
    ]},
    "matrix": {"label": "Matrix", "fields": [
        {"key": "homeserver", "label": "Homeserver", "type": "string", "required": True, "placeholder": "https://matrix.example.org"},
        {"key": "room", "label": "Room", "type": "string", "required": True, "help": "Room id (!abc:example.org) or alias (#ops:example.org); the bot user must have joined it"},
        {"key": "token", "label": "Access token", "type": "secret", "required": True},
    ]},
    "email": {"label": "Email", "fields": [
        {"key": "host", "label": "SMTP server", "type": "string", "required": True},
        {"key": "port", "label": "Port", "type": "int", "default": 587, "min": 1, "max": 65535},
        {"key": "security", "label": "Security", "type": "select", "default": "starttls", "options": ["starttls", "ssl", "none"]},
        {"key": "username", "label": "User name", "type": "string"},
        {"key": "password", "label": "Password", "type": "secret"},
        {"key": "sender", "label": "From", "type": "string", "required": True, "placeholder": "labdex@lab.example.net"},
        {"key": "to", "label": "To", "type": "list", "required": True, "help": "One or more addresses"},
    ]},
    "webhook": {"label": "Webhook", "fields": [
        {"key": "url", "label": "URL", "type": "string", "required": True},
        {"key": "secret", "label": "Signing secret", "type": "secret",
         "help": "If set, X-Labdex-Signature: sha256=<HMAC of the body>"},
    ]},
}


class SendError(Exception):
    pass


def _vault():
    return current_app.extensions["labdex"]["vault"]


def _link(path: str) -> str:
    from . import naming
    base = (naming.get_setting("public_url") or "").rstrip("/")
    return f"{base}{path}" if base and path.startswith("/") else path


# ------------------------------------------------------------------ emitting
def emit(event: str, title: str, body: str = "", link: str = "", data: dict | None = None,
         integration: str | None = None, only: NotificationChannel | None = None, commit: bool = True) -> int:
    """Queue a notification for every channel that wants it. Returns how many were queued."""
    n = 0
    chans = [only] if only is not None else db.session.scalars(
        db.select(NotificationChannel).where(NotificationChannel.enabled.is_(True)))
    for ch in chans:
        if only is None:
            if not any(fnmatchcase(event, p) for p in ch.events or []):
                continue
            if integration is not None and not any(fnmatchcase(integration, g) for g in ch.integrations or ["*"]):
                continue
        db.session.add(Notification(channel_id=ch.id, event=event, title=title[:200], body=body[:4000],
                                    link=_link(link)[:500], data=data or {}))
        n += 1
    if n:
        db.session.commit() if commit else db.session.flush()
        from .plugins import jobs
        jobs.wake()
    return n


def job_event(job, event: str) -> None:
    """plan.waiting / plan.applied / job.failed (and backup.failed) for a job."""
    integ = job.integration
    name = integ.name if integ else "?"
    host = (job.params or {}).get("host")
    what = f"{name} {job.action}" + (f" for {host}" if host else "")
    link = f"/jobs/{job.id}"
    data = {"job": job.id, "integration": name, "action": job.action, "status": job.status, "host": host,
            "trigger": job.trigger, "changes": len(job.plan or [])}
    if event == "plan.waiting":
        pol = job.policy or {}
        need = int(pol.get("approvals", 1)) - len(job.approvals or [])
        emit(event, f"Plan waiting: {what}", f"{len(job.plan or [])} change(s) need {need} approval(s) "
             f"(policy {pol.get('name', 'default')}).", link, data | {"needed": need}, integration=name)
    elif event == "plan.applied":
        emit(event, f"Applied: {what}", f"{len(job.plan or [])} change(s) applied by {job.applied_by or 'policy'}.",
             link, data, integration=name)
    elif event == "job.failed":
        err = (job.result or {}).get("error", "")
        if integ and integ.plugin_id == "backup":
            emit("backup.failed", f"Backup failed: {name}", err, link, data | {"error": err}, integration=name)
        else:
            emit("job.failed", f"Failed: {what}", err, link, data | {"error": err}, integration=name)


# ------------------------------------------------------------------ sending
def _settings(ch: NotificationChannel) -> dict:
    from .plugins import resolved_fields
    return resolved_fields(KINDS[ch.kind]["fields"], ch.settings or {}, _vault())


def send(n: Notification) -> None:
    ch = n.channel
    s = _settings(ch)
    text = n.body + (f"\n{n.link}" if n.link else "")
    try:
        if ch.kind == "ntfy":
            # title and the rest as query parameters: headers can't carry UTF-8
            q = {"title": n.title, "priority": s.get("priority") or "default", "tags": n.event.replace(".", "_")}
            if n.link.startswith("http"):
                q["click"] = n.link
            headers = {"Authorization": f"Bearer {s['token']}"} if s.get("token") else {}
            r = requests.post(f"{s['server'].rstrip('/')}/{quote(s['topic'], safe='')}", params=q,
                              data=(n.body or n.title).encode(), headers=headers, timeout=TIMEOUT)
            _ok(r)
        elif ch.kind == "matrix":
            hs = s["homeserver"].rstrip("/")
            auth = {"Authorization": f"Bearer {s['token']}"}
            room = s["room"]
            if room.startswith("#"):
                r = requests.get(f"{hs}/_matrix/client/v3/directory/room/{quote(room, safe='')}", headers=auth, timeout=TIMEOUT)
                _ok(r)
                room = r.json()["room_id"]
            html = f"<b>{_esc(n.title)}</b><br>{_esc(n.body)}" + (f'<br><a href="{_esc(n.link)}">{_esc(n.link)}</a>' if n.link else "")
            r = requests.put(f"{hs}/_matrix/client/v3/rooms/{quote(room, safe='')}/send/m.room.message/labdex-{n.id}-{uuid.uuid4().hex[:8]}",
                             json={"msgtype": "m.text", "body": f"{n.title}\n{text}", "format": "org.matrix.custom.html",
                                   "formatted_body": html}, headers=auth, timeout=TIMEOUT)
            _ok(r)
        elif ch.kind == "email":
            msg = EmailMessage()
            msg["Subject"] = n.title
            msg["From"] = s["sender"]
            msg["To"] = ", ".join(s["to"])
            msg.set_content(text)
            port = int(s.get("port") or 587)
            if s.get("security") == "ssl":
                smtp = smtplib.SMTP_SSL(s["host"], port, timeout=TIMEOUT, context=ssl.create_default_context())
            else:
                smtp = smtplib.SMTP(s["host"], port, timeout=TIMEOUT)
            with smtp:
                if s.get("security", "starttls") == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if s.get("username"):
                    smtp.login(s["username"], s.get("password", ""))
                smtp.send_message(msg)
        elif ch.kind == "webhook":
            payload = json.dumps({"event": n.event, "title": n.title, "body": n.body, "link": n.link, "data": n.data or {},
                                  "at": n.created_at.isoformat() + "Z", "id": n.id}, sort_keys=True).encode()
            headers = {"Content-Type": "application/json", "User-Agent": "labdex"}
            if s.get("secret"):
                headers["X-Labdex-Signature"] = "sha256=" + hmac.new(s["secret"].encode(), payload, hashlib.sha256).hexdigest()
            r = requests.post(s["url"], data=payload, headers=headers, timeout=TIMEOUT)
            _ok(r)
        else:
            raise SendError(f"unknown channel kind {ch.kind}")
    except SendError:
        raise
    except requests.Timeout as exc:
        raise SendError(f"no answer from {_where(exc)} within {TIMEOUT} s") from exc
    except requests.ConnectionError as exc:
        raise SendError(f"can't connect to {_where(exc)}") from exc
    except (requests.RequestException, smtplib.SMTPException, OSError, KeyError, ValueError) as exc:
        raise SendError(f"{type(exc).__name__}: {exc}") from exc


def _where(exc) -> str:
    from urllib.parse import urlsplit
    url = getattr(getattr(exc, "request", None), "url", "") or ""
    return urlsplit(url).netloc or "the server"


def _ok(r) -> None:
    if r.status_code >= 300:
        raise SendError(f"HTTP {r.status_code}: {r.text[:200]}")


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def deliver(limit: int = 20) -> int:
    """Send what's due. Returns how many went out."""
    now = utcnow()
    sent = 0
    for n in list(db.session.scalars(db.select(Notification).where(Notification.status == "pending", Notification.next_try_at <= now)
                                     .order_by(Notification.id).limit(limit))):
        n.attempts += 1
        try:
            send(n)
            n.status, n.sent_at, n.error = "sent", utcnow(), ""
            sent += 1
        except SendError as exc:
            n.error = str(exc)[:1000]
            if n.attempts > len(RETRY):
                n.status = "failed"
            else:
                n.next_try_at = utcnow() + timedelta(seconds=RETRY[n.attempts - 1])
        db.session.commit()
    return sent


def prune(days: int = 30) -> None:
    db.session.execute(db.delete(Notification).where(Notification.status != "pending",
                                                     Notification.created_at < utcnow() - timedelta(days=days)))
    db.session.commit()


# ------------------------------------------------------------------ channels
def channel_dict(ch: NotificationChannel) -> dict:
    from .plugins import public_fields
    values, secrets_set, refs = public_fields(KINDS[ch.kind]["fields"], ch.settings or {})
    last = db.session.scalar(db.select(Notification).where(Notification.channel_id == ch.id).order_by(Notification.id.desc()).limit(1))
    failed = db.session.scalar(db.select(db.func.count()).select_from(Notification).where(
        Notification.channel_id == ch.id, Notification.status == "failed")) or 0
    return {"id": ch.id, "name": ch.name, "kind": ch.kind, "enabled": ch.enabled, "events": list(ch.events or []),
            "integrations": list(ch.integrations or ["*"]), "settings": values, "secrets_set": secrets_set, "secret_refs": refs,
            "failed": failed,
            "last": {"event": last.event, "status": last.status, "error": last.error,
                     "at": last.created_at.isoformat() + "Z"} if last else None}


def notification_dict(n: Notification) -> dict:
    return {"id": n.id, "channel": n.channel.name if n.channel else None, "event": n.event, "title": n.title, "body": n.body,
            "link": n.link, "status": n.status, "attempts": n.attempts, "error": n.error,
            "created_at": n.created_at.isoformat() + "Z", "sent_at": n.sent_at.isoformat() + "Z" if n.sent_at else None}
