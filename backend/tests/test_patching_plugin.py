"""The Patching (Ansible) plugin: planned from the patch state, applied only after an approval,
reboots only where allowed, reports back. Ansible itself is faked: nothing reaches a host."""
import json
from pathlib import Path

import pytest

from labdex import patches
from labdex.models import Job, db
from labdex.plugins.base import AnsibleResult, Context

DNS, MON, PVE = "srv-vm-dns-01", "srv-vm-mon-01", "srv-pm-pve-02"


@pytest.fixture
def runs(monkeypatch):
    """Every ansible run: (play, limit, inventory hosts). Each host answers with a clean report."""
    seen = []

    def fake(self, playbook=None, *, extravars=None, limit=None, check=False, diff=False, inventory_override=None,
             base_dir=None, roles_path=None, inline=None):
        seen.append({"play": inline, "limit": limit, "hosts": sorted(inventory_override["all"]["hosts"]), "check": check})
        tasks = inline[0]["tasks"]
        dest = next(t for t in tasks if "ansible.builtin.copy" in t)["ansible.builtin.copy"]["dest"]
        for name in limit.split(","):
            Path(dest.replace("{{ inventory_hostname }}", name)).write_text(json.dumps(
                {"host": name, "source": "agent", "os": "Ubuntu 24.04.1 LTS", "packages": [], "pending": 0, "reboot": False}))
        return AnsibleResult(0, len(limit.split(",")), 0, 0, "")
    monkeypatch.setattr(Context, "ansible", fake)
    return seen


@pytest.fixture
def patching(admin):
    r = admin.post("/api/integrations", json={"plugin_id": "patching", "name": "home-patching"})
    assert r.status_code == 201, r.get_json()
    r = admin.put("/api/integrations/home-patching", json={"enabled": True, "settings": {"reboot": "if_needed", "ssh_user": "ansible"}})
    assert r.status_code == 200, r.get_json()
    # anyone may apply anything right away: the plugin must still wait for a person
    r = admin.post("/api/approval-policies", json={"name": "no approvals", "approvals": 0})
    assert r.status_code == 201, r.get_json()
    return admin


def start(c, **params):
    r = c.post("/api/integrations/home-patching/actions/patch", json={"params": params})
    assert r.status_code == 202, r.get_json()
    return c.get(f"/api/jobs/{r.get_json()['id']}").get_json()


def test_plan_comes_from_the_patch_state_and_waits_for_a_person(patching, runs):
    patches.report("agent", {DNS: {"packages": [{"name": "openssl", "security": True}, "curl"], "reboot": True},
                             MON: {"pending": 0},
                             PVE: {"pending": 4, "reboot": True}})
    job = start(patching)
    assert job["status"] == "planned", job
    plan = {c["name"]: c for c in job["plan"]}
    assert set(plan) == {DNS, PVE}                                    # MON has nothing waiting, the rest never reported
    assert plan[DNS]["summary"] == "2 update(s), 1 security; reboots if needed"
    assert "guest(s); not rebooted" in plan[PVE]["summary"]             # a Proxmox node with VMs is never rebooted
    assert job["needed"] == 1 and "at least one approval" in job["policy"] and job["hosts"] == sorted([DNS, PVE])
    assert runs == []                                                    # planning touches no host
    jobrow = db.session.get(Job, job["id"])
    assert {h.host for h in jobrow.hosts} == {DNS, PVE}                 # for zone-based policies

    r = patching.post(f"/api/jobs/{job['id']}/approve", json={"comment": "go"})
    assert r.status_code in (200, 202), r.get_json()
    job = patching.get(f"/api/jobs/{job['id']}").get_json()
    assert job["status"] == "applied", job
    (run,) = runs
    assert set(run["hosts"]) == {DNS, PVE} and set(run["limit"].split(",")) == {DNS, PVE} and not run["check"]
    play = run["play"][0]
    assert play["serial"] == 1 and play["become"] and play["max_fail_percentage"] == 0
    assert play["vars"]["labdex_reboot_hosts"] == [DNS]
    # every host reported back
    assert patches.summary(DNS)["state"] == "current" and patches.summary(PVE)["state"] == "current"
    assert "home-patching" in [s["source"] for s in patches.summary(DNS)["sources"]]


def test_reboots_off_and_filters(patching, runs):
    patches.report("agent", {DNS: {"pending": 2, "reboot": True}, PVE: {"pending": 4, "packages": [{"name": "pve-kernel", "security": True}]}})
    job = start(patching, reboot="never", hosts=[DNS])
    (c,) = job["plan"]
    assert c["name"] == DNS and c["summary"] == "2 update(s); needs a reboot; reboots are off" and not c["data"]["reboot"]
    job = start(patching, security_only=True)
    assert [c["name"] for c in job["plan"]] == [PVE]
    r = patching.post("/api/integrations/home-patching/actions/patch", json={"params": {"hosts": ["nope-01"]}})
    assert patching.get(f"/api/jobs/{r.get_json()['id']}").get_json()["status"] == "failed"


def test_nothing_waiting_means_no_plan(patching, runs):
    patches.report("agent", {DNS: {"pending": 0}})
    job = start(patching)
    assert job["status"] == "succeeded" and job["result"]["changes"] == 0 and runs == []


def test_a_failed_run_still_reports_and_fails(patching, runs, monkeypatch):
    patches.report("agent", {DNS: {"pending": 1}})
    job = start(patching)

    def failing(self, playbook=None, *, inline=None, limit=None, inventory_override=None, **kw):
        return AnsibleResult(2, 0, 1, 0, "")
    monkeypatch.setattr(Context, "ansible", failing)
    patching.post(f"/api/jobs/{job['id']}/approve", json={})
    job = patching.get(f"/api/jobs/{job['id']}").get_json()
    assert job["status"] == "failed" and "the patch run stopped" in (job.get("log") or "")


def test_report_reads_the_state_without_changing_anything(patching, runs):
    r = patching.post("/api/integrations/home-patching/actions/report")
    job = patching.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert job["status"] == "succeeded", job
    (run,) = runs
    assert not run["play"][0]["become"] and DNS in job["result"]["reported"]
    assert patches.summary(DNS)["sources"][0]["source"] == "home-patching"
    assert all("apt" not in t and "ansible.builtin.reboot" not in t for t in run["play"][0]["tasks"])


def test_the_generated_play():
    import importlib.util
    spec = importlib.util.spec_from_file_location("patching_plugin", Path(__file__).parents[2] / "plugins" / "patching" / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (play,) = mod.patch_play(serial=2, autoremove=True, reboot_hosts=["a"], reboot_timeout=600, report_dir="/tmp/x")
    names = [t["name"] for t in play["tasks"]]
    assert names.index("upgrade (apt)") < names.index("reboot") < names.index("read the patch state")
    reboot = next(t for t in play["tasks"] if t["name"] == "reboot")
    assert reboot["when"][0] == "inventory_hostname in labdex_reboot_hosts"
    assert next(t for t in play["tasks"] if t["name"] == "upgrade (apt)")["ansible.builtin.apt"]["autoremove"] is True
    assert mod.agent_path().name == "patch_report.py" and mod.agent_path().is_file()
