"""Proxmox plugin 1.3: start and stop linked guests, change cores and memory, take snapshots.
Each kind is switched on separately, every run waits for a person, and a fake Proxmox answers."""
import pytest

from .fakes import FakeProxmox, FakeServer
from .test_plugins import make_instance, run


@pytest.fixture
def pve(admin):
    fake = FakeProxmox()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.nodes = ["pve-a"]
    fake.guests = [
        {"vmid": 101, "node": "pve-a", "type": "qemu", "name": "srv-vm-web-01", "status": "stopped", "config": {"cores": 2, "memory": "2048"}},
        {"vmid": 102, "node": "pve-a", "type": "lxc", "name": "srv-ct-cache-01", "status": "running", "config": {"cores": 1, "memory": 512}},
        {"vmid": 103, "node": "pve-a", "type": "qemu", "name": "srv-vm-same-01", "status": "running", "config": {"cores": 2, "memory": 2048}},
    ]
    for name, status, ip, vmid in (("srv-vm-web-01", "active", "10.10.100.81", 101), ("srv-ct-cache-01", "offline", "10.10.100.82", 102),
                                   ("srv-vm-same-01", "active", "10.10.100.83", 103)):
        r = admin.post("/api/hosts", json={"name": name, "status": status, "interfaces": [{"network": "srv", "ip4": ip, "primary": True}]})
        assert r.status_code == 201, r.get_json()
    # anyone may apply anything right away: these plans must still wait for a person
    assert admin.post("/api/approval-policies", json={"name": "no approvals", "approvals": 0}).status_code == 201
    yield fake
    srv.close()


def instance(admin, pve, **more):
    make_instance(admin, "proxmox", "pve", {"url": pve.url, "token_id": "labdex@pve!inv", "token_secret": "s3cret"} | more)
    for name, vmid in (("srv-vm-web-01", 101), ("srv-ct-cache-01", 102), ("srv-vm-same-01", 103)):
        r = admin.put(f"/api/hosts/{name}/modules/pve", json={"settings": {"vmid": vmid}})
        assert r.status_code == 200, r.get_json()


def job(admin, jid):
    return admin.get(f"/api/jobs/{jid}").get_json()


def test_off_by_default(admin, pve):
    instance(admin, pve)
    d = job(admin, run(admin, "pve", "apply_guests")["id"])
    assert d["status"] == "failed" and "guest changes are off" in d["result"]["error"]
    r = admin.post("/api/integrations/pve/actions/snapshot", json={"params": {"host": "srv-vm-web-01"}})
    assert r.get_json()["status"] == "failed" and "snapshots are off" in r.get_json()["result"]["error"]
    assert not pve.calls


def test_power_follows_the_inventory_after_an_approval(admin, pve):
    instance(admin, pve, allow_power=True)
    d = job(admin, run(admin, "pve", "apply_guests")["id"])
    assert d["status"] == "planned" and d["needed"] == 1, d
    plan = {c["name"]: c for c in d["plan"]}
    assert set(plan) == {"srv-vm-web-01", "srv-ct-cache-01"}               # srv-vm-same-01 already matches
    assert plan["srv-vm-web-01"]["summary"] == "VM 101 on pve-a: start"
    assert plan["srv-ct-cache-01"]["summary"] == "LXC 102 on pve-a: shut down"
    assert plan["srv-vm-web-01"]["after"]["status"] == "running"
    assert not pve.calls                                                    # planning never writes

    admin.post(f"/api/jobs/{d['id']}/approve", json={"comment": "ok"})
    d = job(admin, d["id"])
    assert d["status"] == "applied" and sorted(d["result"]["changed"]) == ["srv-ct-cache-01", "srv-vm-web-01"], d["log"]
    writes = [p for m, p, f in pve.calls]
    assert sorted(writes) == ["nodes/pve-a/lxc/102/status/shutdown", "nodes/pve-a/qemu/101/status/start"]
    assert not any("snapshot" in p for p in writes)                         # snapshots are off


def test_cores_memory_with_a_snapshot_first(admin, pve):
    instance(admin, pve, allow_resize=True, allow_snapshots=True)
    assert admin.put("/api/hosts/srv-vm-same-01/modules/pve", json={"settings": {"vmid": 103, "cores": 4, "memory": 4096}}).status_code == 200
    assert admin.put("/api/hosts/srv-vm-web-01/modules/pve", json={"settings": {"vmid": 101, "cores": 2}}).status_code == 200
    d = job(admin, run(admin, "pve", "apply_guests")["id"])
    (c,) = d["plan"]                                                        # power is off; web-01 already has 2 cores
    assert c["name"] == "srv-vm-same-01" and c["before"] == {"status": "running", "cores": 2, "memory": 2048}
    assert c["after"] == {"status": "running", "cores": 4, "memory": 4096}
    assert c["summary"].startswith("VM 103 on pve-a: snapshot labdex-") and "cores 2 → 4 cores" in c["summary"]
    assert "memory takes effect at the next restart" in c["summary"]

    admin.post(f"/api/jobs/{d['id']}/approve", json={})
    assert job(admin, d["id"])["status"] == "applied"
    (m1, p1, f1), (m2, p2, f2) = pve.calls
    assert p1 == "nodes/pve-a/qemu/103/snapshot" and f1["snapname"].startswith("labdex-")   # the snapshot comes first
    assert (m2, p2) == ("PUT", "nodes/pve-a/qemu/103/config") and f2 == {"cores": "4", "memory": "4096"}


def test_a_switch_turned_off_after_planning_wins(admin, pve):
    instance(admin, pve, allow_power=True)
    d = job(admin, run(admin, "pve", "apply_guests")["id"])
    assert len(d["plan"]) == 2
    admin.put("/api/integrations/pve", json={"settings": {"allow_power": False}})
    admin.post(f"/api/jobs/{d['id']}/approve", json={})
    assert not pve.calls and "is off now" in job(admin, d["id"])["log"]


def test_snapshot_on_request(admin, pve):
    instance(admin, pve, allow_snapshots=True)
    r = admin.post("/api/integrations/pve/actions/snapshot", json={"params": {"host": "srv-ct-cache-01", "name": "before-upgrade"}})
    d = job(admin, r.get_json()["id"])
    assert d["status"] == "planned" and d["plan"][0]["name"] == "srv-ct-cache-01@before-upgrade", d
    admin.post(f"/api/jobs/{d['id']}/approve", json={})
    assert job(admin, d["id"])["status"] == "applied"
    assert pve.guests[1]["snapshots"] == ["before-upgrade"]
    r = admin.post("/api/integrations/pve/actions/snapshot", json={"params": {"host": "srv-ct-cache-01", "name": "9; rm"}})
    assert r.get_json()["status"] == "failed" and "starting with a letter" in r.get_json()["result"]["error"]
