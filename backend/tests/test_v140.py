"""1.4: Proxmox creates the guests planned in the inventory (plan/apply), and stays read-only otherwise."""
import pytest

from labdex.models import HostModule, db

from .fakes import FakeProxmox, FakeServer
from .test_plugins import make_instance, run
from .test_v03 import host

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample mike@lab"


@pytest.fixture
def pve():
    fake = FakeProxmox()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.nodes = ["srv-pm-pve-01", "pve-b"]
    fake.guests = [{"vmid": 9000, "node": "pve-b", "type": "qemu", "name": "debian-ci", "status": "stopped", "template": 1,
                    "config": {"scsi0": "local-lvm:base-9000-disk-0,size=4G"}}]
    yield fake
    srv.close()


def settings(pve, **more):
    return {"url": pve.url, "token_id": "labdex@pve!inv", "token_secret": "s3cret", "allow_create": True,
            "lxc_template": "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst", "vm_template": "9000",
            "ssh_public_keys": KEY, "nameserver": "10.10.100.53", "searchdomain": "lab.example.net"} | more


def planned(admin, name, ip, **more):
    body = {"name": name, "status": "planned", "runs_on": "srv-pm-pve-01", "function": f"{name} for tests",
            "interfaces": [{"network": "srv", "ip4": ip, "primary": True}]} | more
    r = admin.post("/api/hosts", json=body)
    assert r.status_code == 201, r.get_json()


def plan(admin, name, action="create_guests"):
    job = run(admin, name, action)
    return job, admin.get(f"/api/jobs/{job['id']}").get_json()


def test_off_by_default(admin, pve):
    make_instance(admin, "proxmox", "pve", settings(pve, allow_create=False))
    planned(admin, "srv-ct-web-01", "10.10.100.90")
    job, d = plan(admin, "pve")
    assert job["status"] == "failed" and "guest creation is off" in job["result"]["error"]
    assert not pve.calls


def test_create_container_and_vm(admin, pve):
    make_instance(admin, "proxmox", "pve", settings(pve, disk=12))
    planned(admin, "srv-ct-web-01", "10.10.100.90")
    planned(admin, "srv-vm-app-01", "10.10.100.91", runs_on="", interfaces=[
        {"network": "srv", "ip4": "10.10.100.91", "primary": True, "mac": "bc:24:11:aa:bb:cc"}])
    planned(admin, "srv-vm-nonode-01", "10.10.100.92", runs_on="")
    planned(admin, "srv-ct-active-01", "10.10.100.93", status="active")  # not planned: never touched

    # the VM has no node yet: skipped with a reason, the rest is planned
    job, d = plan(admin, "pve")
    names = {c["name"]: c for c in d["plan"]}
    assert set(names) == {"srv-ct-web-01"}, d["log"]
    assert any("srv-vm-app-01: skipped: runs on no Proxmox node" in line for line in d["log"].splitlines())
    ct = names["srv-ct-web-01"]
    assert ct["op"] == "create" and ct["kind"] == "guest"
    assert ct["summary"].startswith("LXC on srv-pm-pve-01, from local:vztmpl/debian-12") and "vmbr0 tag 100 10.10.100.90/24" in ct["summary"]
    mac = ct["after"]["nics"][0]["mac"]
    assert mac.startswith("bc:24:11:") and ct["after"]["nics"][0]["new_mac"]
    assert not pve.calls  # planning never writes

    # a default node picks up the other two; apply creates all three
    admin.put("/api/integrations/pve", json={"settings": settings(pve, disk=12, default_node="pve-b")})
    job, d = plan(admin, "pve")
    assert {c["name"] for c in d["plan"]} == {"srv-ct-web-01", "srv-vm-app-01", "srv-vm-nonode-01"}
    assert next(c for c in d["plan"] if c["name"] == "srv-ct-web-01")["after"]["nics"][0]["mac"] == mac  # stable between plans
    done = admin.post(f"/api/jobs/{job['id']}/approve").get_json()
    assert done["status"] == "applied" and done["result"] == {"created": 3, "failed": 0}, admin.get(f"/api/jobs/{job['id']}").get_json()["log"]

    writes = {(m, p): f for m, p, f in pve.calls}
    lxc = writes[("POST", "nodes/srv-pm-pve-01/lxc")]
    assert lxc["hostname"] == "srv-ct-web-01" and lxc["rootfs"] == "local-lvm:12" and lxc["ssh-public-keys"] == KEY
    assert lxc["net0"] == f"name=eth0,bridge=vmbr0,hwaddr={mac.upper()},tag=100,ip=10.10.100.90/24,gw=10.10.100.254,ip6=auto"
    assert lxc["nameserver"] == "10.10.100.53" and lxc["searchdomain"] == "lab.example.net" and lxc["unprivileged"] == "1"
    vms = [(p, f) for (m, p), f in writes.items() if p.endswith("/clone")]
    assert {p for p, _ in vms} == {"nodes/pve-b/qemu/9000/clone"}
    cfg = {p: f for m, p, f in pve.calls if p.endswith("/config")}
    vm_cfg = next(f for p, f in cfg.items() if "BC:24:11:AA:BB:CC" in f.get("net0", ""))
    assert vm_cfg["net0"] == "virtio=BC:24:11:AA:BB:CC,bridge=vmbr0,tag=100"
    assert vm_cfg["ipconfig0"] == "ip=10.10.100.91/24,gw=10.10.100.254,ip6=auto"
    assert vm_cfg["sshkeys"] == "ssh-ed25519%20AAAAC3NzaC1lZDI1NTE5AAAAIExample%20mike%40lab"
    assert not any(p.endswith("/resize") for m, p, f in pve.calls)  # VMs keep the template's disk unless the host asks
    assert sum(1 for m, p, f in pve.calls if p.endswith("/status/start")) == 3

    # the inventory follows: linked, active, and the generated MAC written back
    web = host(admin, "srv-ct-web-01")
    assert web["status"] == "active" and web["interfaces"][0]["mac"] == mac
    links = {hm.host_name: hm.settings for hm in db.session.scalars(db.select(HostModule))}
    assert links["srv-ct-web-01"]["node"] == "srv-pm-pve-01" and links["srv-vm-app-01"]["node"] == "pve-b"
    hist = admin.get("/api/history?object_name=srv-ct-web-01").get_json()
    assert hist[0]["summary"].startswith("created in Proxmox (")
    assert host(admin, "srv-ct-active-01")["status"] == "active"

    # created guests aren't planned again, and drift sees them as matching
    job, d = plan(admin, "pve")
    assert job["status"] == "succeeded" and not d["plan"]
    rows = {r["host"]: r for r in (run(admin, "pve", "drift") and admin.get("/api/integrations/pve/pages/guests").get_json()["sections"][0]["rows"])}
    assert "srv-ct-web-01" in rows


def test_one_host_disk_and_failure(admin, pve):
    make_instance(admin, "proxmox", "pve", settings(pve))
    planned(admin, "srv-vm-big-01", "10.10.100.95")
    hm = admin.put("/api/hosts/srv-vm-big-01/modules/pve", json={"settings": {"disk": 40, "cores": 8}})
    assert hm.status_code == 200, hm.get_json()
    r = admin.post("/api/integrations/pve/actions/create_guest", json={"params": {"host": "srv-vm-big-01"}})
    assert r.status_code == 202, r.get_json()
    d = admin.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert d["plan"], (d["log"], d.get("result"), d["status"])
    assert "8 cores" in d["plan"][0]["summary"] and "40 GB" in d["plan"][0]["summary"]
    pve.fail = "start"
    done = admin.post(f"/api/jobs/{d['id']}/approve").get_json()
    assert done["status"] == "failed"
    resize = next(f for m, p, f in pve.calls if p.endswith("/resize"))
    assert resize == {"disk": "scsi0", "size": "40G"}
    # the VM exists but didn't start: linked (so it isn't made twice), offline, and said so
    big = host(admin, "srv-vm-big-01")
    assert big["status"] == "offline" and big["interfaces"][0]["mac"].startswith("bc:24:11:")
    assert db.session.scalar(db.select(HostModule).where(HostModule.host_name == "srv-vm-big-01")).settings["vmid"] == 300
    assert "didn't start" in admin.get(f"/api/jobs/{d['id']}").get_json()["log"]
    pve.fail = ""
    r = admin.post("/api/integrations/pve/actions/create_guest", json={"params": {"host": "srv-vm-big-01"}})
    assert r.get_json()["status"] == "failed" and "only planned hosts" in r.get_json()["result"]["error"]


