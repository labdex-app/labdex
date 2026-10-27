"""v0.8.0: Proxmox, read-only import and drift."""
import pytest

from labdex.models import Host, HostModule, db

from .fakes import FakeProxmox, FakeServer
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


@pytest.fixture
def pve():
    fake = FakeProxmox()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.guests = [
        # already in the inventory (matched by address): gets linked
        {"vmid": 101, "node": "srv-pm-pve-02", "type": "qemu", "name": "srv-vm-dns-01", "status": "running",
         "config": {"net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0,tag=100"},
         "agent": [{"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1"}]},
                   {"name": "eth0", "hardware-address": "bc:24:11:00:00:01",
                    "ip-addresses": [{"ip-address": "10.10.100.53"}, {"ip-address": "fe80::1"}, {"ip-address": "fd00:10:10:100::53"}]}]},
        # new VM with a free-form name and an address in srv
        {"vmid": 102, "node": "srv-pm-pve-01", "type": "qemu", "name": "Paperless", "status": "running",
         "config": {"net0": "virtio=BC:24:11:00:00:02,bridge=vmbr0,tag=100", "description": "Documents\nmore"},
         "agent": [{"name": "eth0", "ip-addresses": [{"ip-address": "10.10.100.77"}]}]},
        # new LXC, stopped, static address in the config
        {"vmid": 200, "node": "srv-pm-pve-01", "type": "lxc", "name": "pihole", "status": "stopped",
         "config": {"net0": "name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:03,ip=10.10.100.78/24,gw=10.10.100.254,tag=100"}},
        # a template: skipped
        {"vmid": 9000, "node": "srv-pm-pve-01", "type": "qemu", "name": "debian-tpl", "status": "stopped", "template": 1, "config": {}},
    ]
    yield fake
    srv.close()


def settings(pve):
    return {"url": pve.url, "token_id": "labdex@pve!inv", "token_secret": "s3cret"}


def test_import_and_drift(admin, pve):
    make_instance(admin, "proxmox", "pve", settings(pve))
    assert run(admin, "pve", "test")["result"]["message"] == "2 node(s), 4 guest(s)"
    plan = run(admin, "pve", "import_guests")
    d = admin.get(f"/api/jobs/{plan['id']}").get_json()
    changes = {c["name"]: c for c in d["plan"]}
    assert set(changes) == {"srv-vm-dns-01", "srv-vm-paperless-01", "srv-ct-pihole-01"}
    assert changes["srv-vm-dns-01"]["op"] == "update" and changes["srv-vm-dns-01"]["kind"] == "link"
    p = changes["srv-vm-paperless-01"]["after"]
    assert p["fun_names"] == ["paperless"] and p["runs_on"] == "srv-pm-pve-01" and p["function"] == "Documents"
    assert p["interfaces"] == [{"mac": "bc:24:11:00:00:02", "network": "srv", "ip4": "10.10.100.77"}]
    assert changes["srv-ct-pihole-01"]["after"]["status"] == "offline"
    done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied" and done["result"] == {"imported": 2, "linked": 1}, admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    paper = host(admin, "srv-vm-paperless-01")
    assert paper["fun"] == "paperless" and paper["runs_on"] == "srv-pm-pve-01"
    links = {hm.host_name: hm.settings for hm in db.session.scalars(db.select(HostModule))}
    assert links["srv-vm-dns-01"] == {"vmid": 101, "node": "srv-pm-pve-02"}
    hist = admin.get("/api/history?object_name=srv-vm-paperless-01").get_json()
    assert hist[0]["summary"] == "imported from Proxmox (102)"
    # nothing left to import
    assert run(admin, "pve", "import_guests")["status"] == "succeeded"

    # drift: the dns VM has a MAC the inventory lacks; the pihole got renamed and moved
    pve.guests[2]["name"], pve.guests[2]["node"] = "dns-filter", "srv-pm-pve-02"
    pve.guests[2]["status"] = "running"
    pve.guests[2]["config"]["net0"] = "name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:03,ip=dhcp,tag=100"
    pve.guests[2]["lxc_ifaces"] = [{"name": "eth0", "hwaddr": "bc:24:11:00:00:03", "inet": "10.10.100.79/24"}]
    job = run(admin, "pve", "drift")
    assert job["result"] == {"guests": 3, "linked": 3, "drifting": 2, "not_imported": 0}, job
    page = admin.get("/api/integrations/pve/pages/guests").get_json()
    rows = {r["host"]: r for r in page["sections"][0]["rows"]}
    assert "MAC not in the inventory: bc:24:11:00:00:01" in rows["srv-vm-dns-01"]["what"]
    ph = rows["srv-ct-pihole-01"]["what"]
    assert "named “dns-filter”" in ph and "runs on srv-pm-pve-02" in ph and "running, but offline" in ph
    assert "address not in the inventory: 10.10.100.79" in ph and "address the guest doesn't have: 10.10.100.78" in ph
    assert rows["srv-vm-paperless-01"]["what"] == "—"
    # explained drift doesn't count
    hm = db.session.scalar(db.select(HostModule).where(HostModule.host_name == "srv-vm-dns-01"))
    hm.settings = {**hm.settings, "explained": "second NIC for the storage network, not in labdex yet"}
    db.session.commit()
    assert run(admin, "pve", "drift")["result"]["drifting"] == 1
    # a deleted guest
    pve.guests = [g for g in pve.guests if g["vmid"] != 102]
    run(admin, "pve", "drift")
    rows = {r["host"]: r for r in admin.get("/api/integrations/pve/pages/guests").get_json()["sections"][0]["rows"]}
    assert rows["srv-vm-paperless-01"]["what"] == "no longer in Proxmox"


def test_bad_token(admin, pve):
    make_instance(admin, "proxmox", "pve", settings(pve) | {"token_secret": "nope"})
    job = run(admin, "pve", "test")
    assert job["status"] == "failed" and "access denied" in job["result"]["error"]
    assert put_host and Host  # (imports used by other tests)


def test_unknown_guests_go_into_discovery(admin, pve):
    make_instance(admin, "proxmox", "pve", settings(pve))
    job = run(admin, "pve", "drift")
    assert job["status"] == "succeeded", job
    from labdex.models import DiscoveredHost
    seen = {d.ip: d for d in db.session.scalars(db.select(DiscoveredHost))}
    paper = seen["10.10.100.77"]  # the running VM labdex doesn't know
    assert paper.hostname == "Paperless" and paper.mac == "bc:24:11:00:00:02" and paper.up
    assert "Proxmox VM 102 on srv-pm-pve-01" == paper.vendor
    assert not seen["10.10.100.78"].up  # the stopped container: known to exist, not seen answering
    assert "10.10.100.53" in seen  # the one labdex has as a host is there too; Discovery shows it as known
    unknown = admin.get("/api/discovery/unknown-hosts").get_json()
    items = unknown["items"] if isinstance(unknown, dict) else unknown
    hit = next(u for u in items if u["ip"] == "10.10.100.77")
    assert hit["hostname"] == "Paperless"  # to adopt from the Unknown addresses tab
    assert not any(u["ip"] == "10.10.100.53" for u in items)  # srv-vm-dns-01 has that address
