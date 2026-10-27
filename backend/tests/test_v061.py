"""v0.6.1/0.6.2: approval policies and change windows."""
from datetime import datetime, timedelta

import pytest

from labdex import approvals
from labdex.models import Job, db, utcnow
from labdex.models import utcnow as utcnow_real
from labdex.plugins import installer

from .test_plugins import write_plugin

DEMO = """
id: demo
name: Demo
actions:
  - {id: change, mode: plan}
  - {id: hostchange, mode: plan, scope: host}
"""
CODE = """
from labdex.plugins.base import Change, Plugin as Base
class Plugin(Base):
    def plan_change(self, params):
        return [Change("update", "thing", "x", "would change x")]
    def apply_change(self, changes, params):
        return {"done": len(changes)}
    def plan_hostchange(self, params):
        return [Change("update", "host", params["host"], "would touch it")]
    def apply_hostchange(self, changes, params):
        return {"done": 1}
"""


@pytest.fixture
def demo(loaded, admin):
    write_plugin(loaded, "demo", DEMO, CODE)
    installer.reload_registry(loaded)
    admin.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
    r = admin.put("/api/integrations/demo1", json={"enabled": True})
    assert r.status_code == 200, r.get_json()
    return admin


def plan(c, action="change", host=None):
    url = f"/api/hosts/{host}/modules/demo1/actions/{action}" if host else f"/api/integrations/demo1/actions/{action}"
    r = c.post(url)
    assert r.status_code == 202, r.get_json()
    return c.get(f"/api/jobs/{r.get_json()['id']}").get_json()


def policy(c, **kw):
    r = c.post("/api/approval-policies", json={"name": kw.pop("name", "p"), **kw})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_default_is_self_approval(demo):
    j = plan(demo)
    assert j["status"] == "planned" and j["needed"] == 1 and j["policy"] == "default" and j["can_approve"]
    r = demo.post(f"/api/jobs/{j['id']}/approve", json={"comment": "looks right"})
    d = r.get_json()
    assert r.status_code == 202 and d["outcome"] == "applying" and d["status"] == "applied"
    assert d["approvals"][0]["by"] == "root" and d["approvals"][0]["comment"] == "looks right"
    assert demo.post(f"/api/jobs/{j['id']}/approve").status_code == 409


def test_two_approvers_from_a_group_not_yourself(demo, make_user):
    alice = make_user("alice", role="operator", group="ops")
    bob = make_user("bob", role="operator")
    demo.put(f"/api/admin/users/{_uid(demo, 'bob')}", json={"groups": ["ops"]})
    carol = make_user("carol", role="operator")
    policy(demo, name="two-eyes", approvals=2, approvers=["@ops"], self_approval=False)
    j = alice.post("/api/integrations/demo1/actions/change").get_json()
    j = alice.get(f"/api/jobs/{j['id']}").get_json()
    assert j["needed"] == 2 and j["policy"] == "two-eyes"
    assert not j["can_approve"] and "someone else" in j["why_not"]  # alice started it
    assert "approvers: @ops" in carol.get(f"/api/jobs/{j['id']}").get_json()["why_not"]
    assert carol.post(f"/api/jobs/{j['id']}/approve").status_code == 409
    assert demo.post(f"/api/jobs/{j['id']}/approve").status_code == 409  # admins too: not in @ops
    r = bob.post(f"/api/jobs/{j['id']}/approve").get_json()
    assert r["outcome"] == "waiting" and r["status"] == "planned" and len(r["approvals"]) == 1
    # the policy in force is the one from planning time
    demo.put(f"/api/approval-policies/{_pid(demo, 'two-eyes')}", json={"approvals": 3})
    policy(demo, name="ops-2", approvals=1)
    assert bob.post(f"/api/jobs/{j['id']}/approve").status_code == 409  # already approved
    ops3 = make_user("dave", role="operator")
    demo.put(f"/api/admin/users/{_uid(demo, 'dave')}", json={"groups": ["ops"]})
    r = ops3.post(f"/api/jobs/{j['id']}/approve").get_json()
    assert r["status"] == "applied" and [a["by"] for a in r["approvals"]] == ["bob", "dave"]


def _uid(c, name):
    return next(u["id"] for u in c.get("/api/admin/users").get_json() if u["username"] == name)


def _pid(c, name):
    return next(p["id"] for p in c.get("/api/approval-policies").get_json()["policies"] if p["name"] == name)


def test_no_approval_needed_and_matching(demo):
    policy(demo, name="hooks-auto", approvals=0, triggers=["hook", "schedule"])
    policy(demo, name="dc-host", approvals=0, zones=["DC_*"], integrations=["demo*"])
    # manual, no host: neither matches (dc-host is for DC zones only)
    assert plan(demo)["policy"] == "default"
    # a host in DC_TC: dc-host, applied without anyone
    j = plan(demo, "hostchange", host="srv-vm-dns-01")
    assert j["policy"] == "dc-host" and j["status"] == "applied" and "not needed" in j["log"]
    # a host outside DC: default
    assert plan(demo, "hostchange", host="cli-nb-admin-01")["policy"] == "default"


def test_windows_and_delay(demo, monkeypatch):
    r = demo.post("/api/change-windows", json={"name": "evening", "days": ["tue", "thu"], "start": "20:00", "end": "22:00",
                                               "timezone": "Europe/Berlin"})
    assert r.status_code == 201, r.get_json()
    win = r.get_json()
    policy(demo, name="night", approvals=0, window_id=win["id"])
    j = plan(demo)
    assert j["status"] == "approved"
    expect = approvals.next_open(win, utcnow())
    if expect > utcnow() + timedelta(seconds=5):  # (the test may run inside the window)
        assert j["apply_after"] == expect.isoformat() + "Z"
        assert approvals.due() == []
    local = expect.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Berlin"))
    assert local.strftime("%a %H:%M") in ("Tue 20:00", "Thu 20:00") or expect <= utcnow() + timedelta(seconds=5)
    # inside the window → applies
    monkeypatch.setattr(approvals, "utcnow", lambda: expect + timedelta(minutes=30))
    assert approvals.due() == [j["id"]]
    db.session.expire_all()
    assert db.session.get(Job, j["id"]).status == "applied"

    # delay: no window, applies 15 minutes after planning
    demo.put(f"/api/approval-policies/{_pid(demo, 'night')}", json={"window_id": None, "delay_minutes": 15})
    monkeypatch.setattr(approvals, "utcnow", lambda: utcnow_real())
    j = plan(demo)
    assert j["status"] == "approved"
    after = datetime.fromisoformat(j["apply_after"][:-1])
    assert timedelta(minutes=14) < after - utcnow() < timedelta(minutes=16)
    # an approved plan can still be discarded
    r = demo.post(f"/api/jobs/{j['id']}/discard", json={"comment": "not tonight"})
    assert r.get_json()["status"] == "discarded" and r.get_json()["result"]["comment"] == "not tonight"


def test_replan_keeps_approvals_when_changes_match(demo, monkeypatch):
    r = demo.post("/api/change-windows", json={"name": "never-now", "days": ["sun"], "start": "03:00", "end": "03:01"})
    policy(demo, name="windowed", approvals=1, window_id=r.get_json()["id"])
    monkeypatch.setattr(approvals, "utcnow", lambda: datetime(2026, 10, 19, 12, 0))  # Monday
    j = plan(demo)
    r = demo.post(f"/api/jobs/{j['id']}/approve").get_json()
    assert r["outcome"] == "scheduled" and r["status"] == "approved"
    # something else changes the inventory and a hook plans again: same changes → approvals stay
    demo.post("/api/aliases", json={"name": "SG_Y", "type": "port", "members": ["1"]})
    j2 = plan(demo)
    assert j2["replaces"] == j["id"] and j2["status"] == "approved" and j2["approvals"][0]["by"] == "root"
    assert demo.get(f"/api/jobs/{j['id']}").get_json()["status"] == "stale"


def test_window_math():
    w = {"days": ["fri"], "start": "23:00", "end": "01:00", "timezone": "UTC"}
    assert approvals.window_open(w, datetime(2026, 10, 23, 23, 30))  # Friday 23:30
    assert approvals.window_open(w, datetime(2026, 10, 24, 0, 30))  # into Saturday
    assert not approvals.window_open(w, datetime(2026, 10, 24, 1, 0))
    assert approvals.next_open(w, datetime(2026, 10, 24, 2, 0)) == datetime(2026, 10, 30, 23, 0)
    assert approvals.next_open(None, datetime(2026, 1, 1)) == datetime(2026, 1, 1)


def test_validation_and_permissions(demo, make_user):
    def errs(url, data):
        r = demo.post(url, json=data)
        assert r.status_code == 422, r.get_json()
        return {e["field"]: e["message"] for e in r.get_json()["errors"]}
    e = errs("/api/change-windows", {"name": "x", "days": ["funday"], "start": "25:00", "end": "2200", "timezone": "Mars/Base"})
    assert set(e) == {"days", "start", "end", "timezone"}
    e = errs("/api/approval-policies", {"name": "", "approvals": 9, "triggers": ["cron"], "approvers": ["nobody", "@nogroup"], "window_id": 99})
    assert set(e) == {"name", "approvals", "triggers", "approvers", "window_id"}
    policy(demo, name="dup")
    assert "exists" in errs("/api/approval-policies", {"name": "dup"})["name"]
    op = make_user("olga", role="operator")
    assert op.get("/api/approval-policies").status_code == 200
    assert op.post("/api/approval-policies", json={"name": "mine"}).status_code == 403
    assert op.post("/api/change-windows", json={"name": "mine"}).status_code == 403
    # deleting a window leaves its policies without one
    w = demo.post("/api/change-windows", json={"name": "w"}).get_json()
    demo.put(f"/api/approval-policies/{_pid(demo, 'dup')}", json={"window_id": w["id"]})
    assert demo.get("/api/change-windows").get_json()[0]["policies"] == ["dup"]
    assert demo.delete(f"/api/change-windows/{w['id']}").status_code == 204
    assert demo.get("/api/approval-policies").get_json()["policies"][0]["window_id"] is None
