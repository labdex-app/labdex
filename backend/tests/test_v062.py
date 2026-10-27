"""v0.6.2: notifications."""
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from labdex import notify
from labdex.models import Notification, db
from labdex.plugins import installer

from .test_plugins import write_plugin

DEMO = """
id: demo
name: Demo
actions:
  - {id: change, mode: plan}
  - {id: boom, mode: run}
  - {id: watch, mode: run}
"""
CODE = """
from labdex.plugins.base import Change, Plugin as Base
class Plugin(Base):
    def plan_change(self, params):
        return [Change("update", "thing", "x", "would change x")]
    def apply_change(self, changes, params):
        return {"done": 1}
    def run_boom(self, params):
        raise RuntimeError("disk full")
    def run_watch(self, params):
        self.ctx.notify("host.down", "srv-vm-dns-01 is down", "no answer for 5 minutes", "/hosts/srv-vm-dns-01")
        self.ctx.notify("quota", "almost full")
        return {}
"""


class Sink:
    """A tiny HTTP server that records what it gets."""

    def __init__(self, status=200):
        self.got, self.status = [], status
        sink = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                sink.got.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": self.rfile.read(n)})
                body = b'{"room_id": "!room:example.org"}' if "directory" in self.path else b"{}"
                self.send_response(sink.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_POST = do_PUT = do_GET = _do

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def sink():
    s = Sink()
    yield s
    s.close()


@pytest.fixture
def demo(loaded, admin):
    write_plugin(loaded, "demo", DEMO, CODE)
    installer.reload_registry(loaded)
    admin.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
    admin.put("/api/integrations/demo1", json={"enabled": True})
    admin.put("/api/admin/settings", json={"public_url": "https://labdex.example.net/"})
    return admin


def channel(c, **kw):
    r = c.post("/api/notification-channels", json=kw)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_events_reach_a_webhook(demo, sink):
    ch = channel(demo, name="hook", kind="webhook", settings={"url": sink.url + "/in", "secret": "s3cret"},
                 events=["plan.*", "job.failed", "host.down"])
    assert ch["settings"]["secret"] == "" and ch["secrets_set"] == ["secret"]
    j = demo.post("/api/integrations/demo1/actions/change").get_json()
    demo.post("/api/integrations/demo1/actions/boom")
    demo.post(f"/api/jobs/{j['id']}/approve")
    demo.post("/api/integrations/demo1/actions/watch")
    assert notify.deliver() == 4
    got = [json.loads(x["body"]) for x in sink.got]
    assert [g["event"] for g in got] == ["plan.waiting", "job.failed", "plan.applied", "host.down"]
    assert got[0]["link"] == f"https://labdex.example.net/jobs/{j['id']}" and got[0]["data"]["needed"] == 1
    assert got[1]["body"] == "RuntimeError: disk full" and "demo1 boom" in got[1]["title"]
    sig = sink.got[0]["headers"]["X-Labdex-Signature"]
    assert sig == "sha256=" + hmac.new(b"s3cret", sink.got[0]["body"], hashlib.sha256).hexdigest()
    # plugin events that aren't host.* are namespaced, and nobody subscribed to this one
    assert db.session.scalar(db.select(db.func.count()).select_from(Notification)) == 4
    # an integration filter
    demo.put(f"/api/notification-channels/{ch['id']}", json={"integrations": ["other*"]})
    demo.post("/api/integrations/demo1/actions/boom")
    assert notify.deliver() == 0


def test_retries_then_gives_up(demo):
    bad = Sink(status=500)
    try:
        ch = channel(demo, name="flaky", kind="webhook", settings={"url": bad.url}, events=["job.failed"])
        demo.post("/api/integrations/demo1/actions/boom")
        assert notify.deliver() == 0
        n = db.session.scalar(db.select(Notification))
        assert n.status == "pending" and n.attempts == 1 and "HTTP 500" in n.error
        for _ in range(len(notify.RETRY)):
            n.next_try_at = notify.utcnow()
            db.session.commit()
            notify.deliver()
        assert n.status == "failed" and n.attempts == len(notify.RETRY) + 1
        assert demo.get("/api/notification-channels").get_json()["channels"][0]["failed"] == 1
        assert demo.get("/api/notifications?status=failed").get_json()[0]["channel"] == ch["name"]
    finally:
        bad.close()


def test_ntfy_matrix_and_test_button(demo, sink):
    nt = channel(demo, name="phone", kind="ntfy", settings={"server": sink.url, "topic": "lab ops", "token": "tk", "priority": "high"},
                 events=["test"])
    r = demo.post(f"/api/notification-channels/{nt['id']}/test")
    assert r.status_code == 200 and r.get_json()["status"] == "sent"
    req = sink.got[-1]
    assert req["path"].startswith("/lab%20ops?") and "title=labdex+test+notification" in req["path"] and "priority=high" in req["path"]
    assert req["headers"]["Authorization"] == "Bearer tk" and b"works" in req["body"]

    mx = channel(demo, name="room", kind="matrix", settings={"homeserver": sink.url, "room": "#ops:example.org", "token": "mx"},
                 events=["job.failed"])
    demo.post("/api/integrations/demo1/actions/boom")
    assert notify.deliver() == 1
    put = sink.got[-1]
    assert put["method"] == "PUT" and "/rooms/%21room%3Aexample.org/send/m.room.message/" in put["path"]
    assert json.loads(put["body"])["msgtype"] == "m.text" and put["headers"]["Authorization"] == "Bearer mx"
    # a failing test says why
    demo.put(f"/api/notification-channels/{mx['id']}", json={"settings": {"homeserver": "http://127.0.0.1:9", "room": "!r:x", "token": ""}})
    r = demo.post(f"/api/notification-channels/{mx['id']}/test")
    assert r.status_code == 502 and r.get_json()["error"] == "can't connect to 127.0.0.1:9"


def test_email(demo, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, **kw):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            sent.append("starttls")

        def login(self, u, p):
            sent.append(("login", u, p))

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    channel(demo, name="mail", kind="email", settings={"host": "mail.example.net", "username": "lab", "password": "pw",
                                                       "sender": "labdex@example.net", "to": "me@example.net, you@example.net"},
            events=["job.failed"])
    demo.post("/api/integrations/demo1/actions/boom")
    assert notify.deliver() == 1
    assert sent[0] == "starttls" and sent[1] == ("login", "lab", "pw")
    msg = sent[2]
    assert msg["To"] == "me@example.net, you@example.net" and msg["Subject"].startswith("Failed: demo1 boom")


def test_backup_failed_event(app):
    from labdex.models import Integration, Job
    ch = notify.NotificationChannel(name="b", kind="webhook", settings={}, events=["backup.failed"], integrations=["*"])
    db.session.add(ch)
    integ = Integration(plugin_id="backup", name="nightly", enabled=True, settings={}, state={}, selectors=[])
    db.session.add(integ)
    db.session.commit()
    job = Job(integration_id=integ.id, action="backup", mode="run", status="failed", result={"error": "no space"})
    db.session.add(job)
    db.session.commit()
    notify.job_event(job, "job.failed")
    n = db.session.scalar(db.select(Notification))
    assert n.event == "backup.failed" and n.title == "Backup failed: nightly" and n.body == "no space"


def test_validation_and_admin_only(demo, make_user):
    def errs(data):
        r = demo.post("/api/notification-channels", json=data)
        assert r.status_code == 422, r.get_json()
        return {e["field"]: e["message"] for e in r.get_json()["errors"]}
    assert "one of" in errs({"name": "x", "kind": "pigeon"})["kind"]
    e = errs({"name": "x", "kind": "ntfy", "settings": {"server": "ftp://x"}, "events": ["nope.*"]})
    assert set(e) == {"settings.topic", "settings.server", "events"}
    e = errs({"name": "", "kind": "email", "settings": {"host": "m", "port": 99999}})
    assert {"name", "settings.port", "settings.sender", "settings.to"} <= set(e)
    assert demo.put("/api/admin/settings", json={"public_url": "labdex.lan"}).status_code == 422
    op = make_user("op", role="operator")
    assert op.get("/api/notification-channels").status_code == 403
    assert op.post("/api/notification-channels", json={"name": "x", "kind": "webhook"}).status_code == 403
