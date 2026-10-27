"""v0.6.0: the job queue, worker processes and run history per host."""
import time
from datetime import timedelta

from labdex.models import Job, JobHost, Worker, db, utcnow
from labdex.plugins import jobs

from .test_plugins import drop_in_app

DEMO = """
id: demo
name: Demo
actions:
  - {id: hello, mode: run}
  - {id: change, mode: plan}
  - {id: poke, mode: run, scope: host}
"""
CODE = """
from labdex.plugins.base import Change, Plugin as Base
class Plugin(Base):
    def run_hello(self, params):
        return {"ok": True}
    def plan_change(self, params):
        return [Change("update", "thing", "x", "would change x")]
    def apply_change(self, changes, params):
        return {"done": len(changes)}
    def run_poke(self, params):
        return {"poked": params["host"]}
"""


def _wait(app, d, job_id, statuses, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        d.step()
        db.session.expire_all()
        j = db.session.get(Job, job_id)
        if j.status in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} is {j.status}")


def test_external_worker_runs_the_queue(tmp_path):
    app, c, ctx = drop_in_app(tmp_path, DEMO, CODE)
    try:
        app.config.update(LABDEX_JOBS_SYNC=False, LABDEX_WORKER="external")
        c.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
        c.put("/api/integrations/demo1", json={"enabled": True})
        r = c.post("/api/integrations/demo1/actions/hello")
        assert r.status_code == 202
        job_id = r.get_json()["id"]
        j = db.session.get(Job, job_id)
        assert (j.status, j.worker, j.phase) == ("queued", None, "run")
        q = c.get("/api/jobs/queue").get_json()
        assert q["mode"] == "external" and q["waiting"] == 1 and not q["ok"]  # nobody to run it yet

        d = jobs.Dispatcher(app, "external", slots=2, scheduler=False)
        j = _wait(app, d, job_id, ("succeeded", "failed"))
        assert j.status == "succeeded" and j.worker == d.id and j.result == {"ok": True}
        d.last_beat = 0
        d.step()  # heartbeat registers the worker
        q = c.get("/api/jobs/queue").get_json()
        assert q["ok"] and q["waiting"] == 0 and q["workers"][0]["mode"] == "external"

        # plan in the worker, apply queued again for any worker
        plan_id = c.post("/api/integrations/demo1/actions/change").get_json()["id"]
        assert _wait(app, d, plan_id, ("planned",)).status == "planned"
        r = c.post(f"/api/jobs/{plan_id}/apply")
        assert r.status_code == 202 and r.get_json()["status"] == "applying" and r.get_json()["worker"] is None
        j = _wait(app, d, plan_id, ("applied", "failed"))
        assert j.status == "applied" and j.result == {"done": 1}
        d.pool.shutdown(wait=True)
    finally:
        ctx.pop()


def test_lost_jobs_fail(app):
    old = utcnow() - timedelta(seconds=jobs.STALE + 5)
    a = Job(action="x", mode="run", status="running", worker="gone:1", heartbeat_at=old, phase="run")
    b = Job(action="x", mode="run", status="running", worker="alive:1", heartbeat_at=utcnow(), phase="run")
    legacy = Job(action="x", mode="run", status="running", phase="")
    db.session.add_all([a, b, legacy])
    db.session.commit()
    assert jobs.recover_stale() == 2
    assert (a.status, b.status, legacy.status) == ("failed", "running", "failed")
    assert "gone:1" in a.result["error"] and "stopped" in a.log

    # a restarted worker with the same id gives up what it held
    mine = Job(action="x", mode="run", status="running", worker=jobs.worker_id(), heartbeat_at=utcnow(), phase="run")
    db.session.add(mine)
    db.session.commit()
    mine_id = mine.id
    jobs.Dispatcher(app, "inline", scheduler=False)._recover_own()
    assert db.session.get(Job, mine_id).status == "failed"


def test_claim_is_exclusive(app):
    j = Job(action="x", mode="run", status="queued", phase="run")
    db.session.add(j)
    db.session.commit()
    assert jobs._claim(j.id, "w1") == "run"
    assert jobs._claim(j.id, "w2") is None
    db.session.expire_all()
    assert db.session.get(Job, j.id).worker == "w1"


def test_run_history_per_host(tmp_path):
    app, c, ctx = drop_in_app(tmp_path, DEMO, CODE)
    try:
        c.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
        c.put("/api/integrations/demo1", json={"enabled": True})
        r = c.post("/api/hosts/srv-vm-dns-01/modules/demo1/actions/poke")
        assert r.status_code == 202, r.get_json()
        jid = r.get_json()["id"]
        c.post("/api/integrations/demo1/actions/hello")
        runs = c.get("/api/jobs?host=srv-vm-dns-01").get_json()
        assert [x["id"] for x in runs] == [jid] and runs[0]["hosts"] == ["srv-vm-dns-01"]
        assert c.get("/api/jobs?host=srv-vm-k3s-01").get_json() == []
        # the history follows a rename
        h = c.get("/api/hosts/srv-vm-dns-01").get_json()
        r = c.put("/api/hosts/srv-vm-dns-01", json={**h, "name": "srv-vm-dns-09"})
        assert r.status_code == 200, r.get_json()
        assert [x["id"] for x in c.get("/api/jobs?host=srv-vm-dns-09").get_json()] == [jid]
    finally:
        ctx.pop()


def test_migration_backfills_job_hosts(app):
    from alembic import command
    from sqlalchemy import text
    from labdex import schema
    db.session.add(Job(action="x", mode="run", status="succeeded", params={"host": "srv-vm-dns-01"}))
    db.session.add(Job(action="y", mode="run", status="succeeded", params={}))
    db.session.commit()
    db.session.remove()
    with schema.migration_connection() as conn:
        command.downgrade(schema.config(conn), "0007_service_details")
    with db.engine.begin() as conn:
        assert "worker" not in {c[1] for c in conn.execute(text("PRAGMA table_info(job)"))} if conn.dialect.name == "sqlite" else True
    with schema.migration_connection() as conn:
        command.upgrade(schema.config(conn), "head")
    assert [(h.job_id, h.host) for h in db.session.scalars(db.select(JobHost))] == [(1, "srv-vm-dns-01")]
    assert db.session.scalar(db.select(db.func.count()).select_from(Worker)) == 0
