"""Drift: a scheduled plan that finds changes after one that found none says so once, and
/api/drift shows every plan action as matching, differing, failed or not checked."""
import pytest

from labdex.models import Integration, Notification, NotificationChannel, db
from labdex.plugins import jobs

from .test_plugins import drop_in_app

SYNC = """
id: demo
name: Demo
actions:
  - {id: sync, label: Sync records, mode: plan, schedulable: true}
  - {id: hello, mode: run}
"""
SYNC_CODE = """
from labdex.plugins.base import Change, Plugin as Base, PluginError
class Plugin(Base):
    def plan_sync(self, params):
        n = int(self.ctx.state.get("differs") or 0)
        if n < 0:
            raise PluginError("the DNS server didn't answer")
        return [Change("update", "record", f"r{i}", "differs") for i in range(n)]
    def apply_sync(self, changes, params):
        return {"done": len(changes)}
    def run_hello(self, params):
        return {}
"""


@pytest.fixture
def lab(tmp_path):
    app, c, ctx = drop_in_app(tmp_path, SYNC, SYNC_CODE)
    c.post("/api/integrations", json={"plugin_id": "demo", "name": "dns"})
    assert c.put("/api/integrations/dns", json={"enabled": True, "settings": {"schedule_sync": 1440}}).status_code == 200
    db.session.add(NotificationChannel(name="ops", kind="webhook", settings={"url": "https://hooks.example.com/x"},
                                       events=["drift.*"]))
    db.session.commit()
    try:
        yield c
    finally:
        ctx.pop()


def differs(n):
    integ = db.session.scalar(db.select(Integration).where(Integration.name == "dns"))
    integ.state = dict(integ.state or {}, plugin={"differs": n})
    db.session.commit()
    return integ


def scheduled(n):
    job = jobs.start(differs(n), "sync", trigger="schedule", created_by="scheduler")
    db.session.expire_all()
    return job


def events():
    return [n.event for n in db.session.scalars(db.select(Notification).order_by(Notification.id))]


def test_drift_is_said_once_and_cleared_once(lab):
    c = lab
    assert c.get("/api/drift").get_json()["items"][0]["state"] == "unchecked"
    scheduled(0)
    item = c.get("/api/drift").get_json()["items"][0]
    assert item["state"] == "in_sync" and item["schedule_minutes"] == 1440 and item["label"] == "Sync records"
    assert events() == []

    first = scheduled(2)
    scheduled(3)                                            # still differs: no second message
    d = c.get("/api/drift").get_json()
    item = d["items"][0]
    assert item["state"] == "drift" and item["changes"] == 3 and d["summary"]["drift"] == 1
    assert item["since"] == db.session.get(type(first), first.id).created_at.isoformat() + "Z"
    assert events() == ["drift.found"]
    n = db.session.scalar(db.select(Notification))
    assert n.title == "Drift: dns differs from labdex" and "3 change(s)" not in n.body and "2 change(s)" in n.body

    scheduled(-1)                                           # a failed check changes nothing
    assert c.get("/api/drift").get_json()["items"][0]["state"] == "failed"
    assert events() == ["drift.found"]
    scheduled(0)
    assert events() == ["drift.found", "drift.cleared"]
    assert c.get("/api/drift").get_json()["items"][0]["state"] == "in_sync"


def test_manual_plans_count_for_the_state_but_never_notify(lab):
    c = lab
    jobs.start(differs(1), "sync", trigger="manual", created_by="admin")
    assert c.get("/api/drift").get_json()["items"][0]["state"] == "drift"
    assert events() == []
