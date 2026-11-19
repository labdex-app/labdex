"""Workflows: steps in order (actions, templates, waits, health checks), each plan with its own
approvals, inside a maintenance window, on a schedule. A drop-in plugin stands in for real ones."""
from datetime import datetime, timedelta

import pytest

from labdex import workflows
from labdex.models import HostHealth, Job, Workflow, WorkflowRun, db

from .test_plugins import drop_in_app

FLOW = """
id: demo
name: Demo
actions:
  - {id: snapshot, mode: run}
  - {id: patch, mode: plan}
  - {id: noop, mode: plan}
  - {id: boom, mode: run}
"""
FLOW_CODE = """
from labdex.plugins.base import Change, Plugin as Base, PluginError
class Plugin(Base):
    def run_snapshot(self, params):
        return {"snapshot": "labdex-test"}
    def plan_patch(self, params):
        return [Change("update", "host", "srv-vm-dns-01", "3 updates", data={"hosts": ["srv-vm-dns-01"]})]
    def apply_patch(self, changes, params):
        return {"patched": [c.name for c in changes]}
    def plan_noop(self, params):
        return []
    def run_boom(self, params):
        raise PluginError("the snapshot store is full")
"""
NOW = datetime(2026, 11, 17, 21, 0)  # a Tuesday, 21:00 UTC


@pytest.fixture
def lab(tmp_path):
    app, c, ctx = drop_in_app(tmp_path, FLOW, FLOW_CODE)
    c.post("/api/integrations", json={"plugin_id": "demo", "name": "home"})
    assert c.put("/api/integrations/home", json={"enabled": True}).status_code == 200
    try:
        yield c
    finally:
        ctx.pop()


def make(c, steps, **more):
    r = c.post("/api/workflows", json={"name": "home vms", "steps": steps, **more})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def run(c, wid):
    r = c.post(f"/api/workflows/{wid}/run")
    assert r.status_code == 202, r.get_json()
    return r.get_json()


def test_steps_run_in_order_and_a_plan_waits_for_a_person(lab):
    c = lab
    wf = make(c, [{"integration": "home", "action": "snapshot"},
                  {"integration": "home", "action": "patch", "name": "patch the VMs"},
                  {"integration": "home", "action": "noop"}])
    assert [s["kind"] for s in wf["steps"]] == ["action"] * 3 and wf["steps"][1]["name"] == "patch the VMs"
    r = run(c, wf["id"])
    assert r["status"] == "waiting", r
    assert [s["status"] for s in r["steps"]] == ["done", "waiting", "pending"]
    plan_id = r["steps"][1]["job_id"]
    job = c.get(f"/api/jobs/{plan_id}").get_json()
    assert job["status"] == "planned"
    assert job["workflow"] == {"run_id": r["id"], "name": "home vms", "step": 1, "of": 3, "status": "waiting"}  # for the Changes page
    assert c.post(f"/api/workflows/{wf['id']}/run").status_code == 409            # one run at a time

    assert c.post(f"/api/jobs/{plan_id}/approve", json={"comment": "ok"}).status_code == 202
    r = c.get(f"/api/workflow-runs/{r['id']}").get_json()
    assert r["status"] == "succeeded", r
    assert [s["status"] for s in r["steps"]] == ["done", "done", "done"]
    assert r["steps"][2]["note"] == "Nothing to change"                           # an empty plan counts as done
    assert c.get("/api/workflows").get_json()[0]["last_run"]["status"] == "succeeded"


def test_a_failed_step_stops_the_run_unless_it_may_continue(lab):
    c = lab
    wf = make(c, [{"integration": "home", "action": "boom", "name": "snapshot"}, {"integration": "home", "action": "patch"}])
    r = run(c, wf["id"])
    assert r["status"] == "failed" and "the snapshot store is full" in r["note"]
    assert [s["status"] for s in r["steps"]] == ["failed", "skipped"]
    assert db.session.scalar(db.select(Job).where(Job.action == "patch")) is None  # never planned

    c.put(f"/api/workflows/{wf['id']}", json={"steps": [{"integration": "home", "action": "boom", "on_failure": "continue"},
                                                          {"integration": "home", "action": "noop"}]})
    r = run(c, wf["id"])
    assert r["status"] == "succeeded" and [s["status"] for s in r["steps"]] == ["failed", "done"]


def test_a_discarded_plan_fails_its_step_and_cancel_discards_a_waiting_plan(lab):
    c = lab
    wf = make(c, [{"integration": "home", "action": "patch"}])
    r = run(c, wf["id"])
    c.post(f"/api/jobs/{r['steps'][0]['job_id']}/discard", json={})
    r = c.get(f"/api/workflow-runs/{r['id']}").get_json()
    assert r["status"] == "failed" and r["steps"][0]["note"] == "the plan was discarded"

    r = run(c, wf["id"])
    jid = r["steps"][0]["job_id"]
    r = c.post(f"/api/workflow-runs/{r['id']}/cancel").get_json()
    assert r["status"] == "cancelled" and r["steps"][0]["status"] == "cancelled"
    assert c.get(f"/api/jobs/{jid}").get_json()["status"] == "discarded"


def test_wait_and_check_steps(lab):
    c = lab
    wf = make(c, [{"integration": "home", "action": "patch"}, {"kind": "wait", "minutes": 5},
                  {"kind": "check", "timeout_minutes": 10}])
    r = run(c, wf["id"])
    c.post(f"/api/jobs/{r['steps'][0]['job_id']}/approve", json={})
    run_ = db.session.get(WorkflowRun, r["id"])
    assert workflows.advance(run_) == "running"                                     # the plan applied: the wait starts
    t0 = workflows._parse(run_.steps[1]["state"]["started_at"])
    assert workflows.advance(run_, t0 + timedelta(minutes=1)) == "running"         # still waiting 5 minutes
    db.session.add(HostHealth(host="srv-vm-dns-01", state="down"))
    db.session.commit()
    assert workflows.advance(run_, t0 + timedelta(minutes=6)) == "running"         # waited; now the check
    assert run_.steps[2]["state"]["hosts"] == ["srv-vm-dns-01"]                     # the hosts the plan named
    assert run_.steps[2]["state"]["note"] == "waiting for srv-vm-dns-01"
    db.session.get(HostHealth, "srv-vm-dns-01").state = "up"
    db.session.commit()
    assert workflows.advance(run_, t0 + timedelta(minutes=7)) == "succeeded"

    # a host that never comes back fails the check after its timeout
    db.session.get(HostHealth, "srv-vm-dns-01").state = "down"
    db.session.commit()
    c.put(f"/api/workflows/{wf['id']}", json={"steps": [{"kind": "check", "hosts": ["srv-vm-dns-01"], "timeout_minutes": 10}]})
    r = run(c, wf["id"])
    run_ = db.session.get(WorkflowRun, r["id"])
    t0 = workflows._parse(run_.steps[0]["state"]["started_at"])
    assert workflows.advance(run_, t0 + timedelta(minutes=11)) == "failed"
    assert "not answering: srv-vm-dns-01" in run_.note


def test_maintenance_window_and_schedule(lab):
    c = lab
    win = c.post("/api/change-windows", json={"name": "evening", "days": ["tue"], "start": "20:00", "end": "22:00", "timezone": "UTC"})
    assert win.status_code == 201, win.get_json()
    wf = make(c, [{"integration": "home", "action": "noop"}, {"kind": "wait", "minutes": 90}, {"integration": "home", "action": "noop"}],
              window="evening", schedule="weekly", days=["tue"], at="20:30", timezone="UTC")
    assert wf["window"] == "evening"
    w = db.session.get(Workflow, wf["id"])
    nxt = workflows.next_run(w, datetime(2026, 11, 16, 9, 0))  # Monday morning → Tuesday 20:30
    assert nxt == datetime(2026, 11, 17, 20, 30)

    # started outside the window: nothing happens until it opens
    w.next_run_at = datetime(2026, 11, 17, 8, 0)
    db.session.commit()
    out = workflows.tick(datetime(2026, 11, 17, 8, 0))
    run_ = db.session.get(WorkflowRun, out["started"][0])
    assert run_.trigger == "schedule"
    assert run_.status == "waiting" and run_.note.startswith("waiting for the maintenance window evening")
    assert db.session.scalar(db.select(Job).where(Job.workflow_run_id == run_.id)) is None
    workflows.tick(NOW)                                                              # 21:00: open
    assert run_.steps[0]["state"]["status"] == "done" and run_.steps[1]["state"]["status"] == "running"
    workflows.tick(NOW + timedelta(minutes=95))                                      # 22:35: the wait is over, the window shut
    assert run_.step == 2 and run_.status == "waiting" and "maintenance window" in run_.note
    workflows.tick(NOW + timedelta(days=7))                                          # the next Tuesday evening
    assert run_.status == "succeeded"


def test_validation_and_templates(lab):
    c = lab
    r = c.post("/api/workflows", json={"name": "x", "steps": [{"kind": "wait"}, {"integration": "nope", "action": "a"},
                                                              {"template": "missing"}, {"kind": "check", "hosts": ["ghost"]},
                                                              {"kind": "dance"}]})
    assert r.status_code == 422
    fields = {e["field"] for e in r.get_json()["errors"]}
    assert {"steps[0].minutes", "steps[1].integration", "steps[2].template", "steps[3].hosts", "steps[4].kind"} <= fields
    assert c.post("/api/workflows", json={"name": "x", "steps": []}).status_code == 422
    assert c.post("/api/workflows", json={"name": "x", "steps": [{"kind": "wait", "minutes": 1}], "window": "nope"}).status_code == 422

    # a template as a step
    t = c.post("/api/job-templates", json={"name": "patch home", "integration": "home", "action": "patch"})
    assert t.status_code == 201, t.get_json()
    wf = make(c, [{"template": "patch home"}])
    r = run(c, wf["id"])
    job = db.session.get(Job, r["steps"][0]["job_id"])
    assert job.template_id == t.get_json()["id"] and job.created_by == f"workflow home vms (run #{r['id']})"
