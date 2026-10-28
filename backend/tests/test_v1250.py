"""1.25: what breaks if a host goes down (the impact view)."""
from .test_v1170 import cable
from .test_v1240 import panel


def lab(admin):
    """firewall and OOB router (the gateways) → core switch → patch panel → both hypervisors;
    core → access switch → the admin PC."""
    r = admin.post("/api/hosts", json={"name": "net-sw-access-01", "zone": "DC_HT", "function": "access switch",
                                       "interfaces": [{"network": "mgmt", "ip4": "10.10.3.3"}]})
    assert r.status_code == 201, r.get_json()
    panel(admin, "net-pp-rack-01", ports=2)
    cable(admin, "net-fw-edge-01", "lan", "net-sw-core-01", "ge-0/0/1")
    cable(admin, "net-rt-oob-01", "eth0", "net-sw-core-01", "ge-0/0/2")
    cable(admin, "net-sw-core-01", "ge-0/0/10", "net-pp-rack-01", "1")
    cable(admin, "net-pp-rack-01", "r1", "srv-pm-pve-01", "eno1")
    cable(admin, "net-sw-core-01", "ge-0/0/11", "net-pp-rack-01", "2")
    cable(admin, "net-pp-rack-01", "r2", "srv-pm-pve-02", "eno1")
    cable(admin, "net-sw-core-01", "ge-0/0/20", "net-sw-access-01", "uplink")
    cable(admin, "net-sw-access-01", "p5", "cli-pc-admin-01", "eth0")


def imp(admin, *names):
    r = admin.get("/api/impact?" + "&".join(f"host={n}" for n in names))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_guests_go_down_with_their_hypervisor(admin):
    r = imp(admin, "srv-pm-pve-02")
    down = {x["host"]: x["because"] for x in r["down"]}
    assert down["srv-pm-pve-02"] == "chosen" and down["srv-vm-dns-01"] == "runs on srv-pm-pve-02"
    assert {"srv-vm-mon-01", "dmz-vm-docker-01"} <= set(down) and "srv-vm-ha-01" not in down
    assert r["dns"] == {"state": "down", "affected": ["srv-vm-dns-01"], "left": []}  # the only DNS server
    assert {("srv-vm-dns-01", "dns"), ("srv-vm-mon-01", "grafana")} <= {(s["host"], s["name"]) for s in r["services"]}


def test_a_switch_cuts_off_what_hangs_on_it(admin):
    lab(admin)
    r = imp(admin, "net-sw-core-01")
    cut = {x["host"]: x["because"] for x in r["cut_off"]}
    # both hypervisors (through the panel), their VMs, the access switch and the PC behind it
    assert {"srv-pm-pve-01", "srv-pm-pve-02", "srv-vm-ha-01", "srv-vm-dns-01", "net-sw-access-01", "cli-pc-admin-01"} <= set(cut)
    assert cut["srv-vm-ha-01"] == "only cabled through net-sw-core-01"
    assert "net-fw-edge-01" not in cut and "net-rt-oob-01" not in cut  # the gateways themselves
    assert r["dns"]["state"] == "down"

    r = imp(admin, "net-sw-access-01")
    assert [x["host"] for x in r["cut_off"]] == ["cli-pc-admin-01"]  # nothing else hangs on it


def test_a_panel_keeps_its_runs_apart(admin):
    """Both hypervisors are cabled through one panel: one going down doesn't take the other."""
    lab(admin)
    r = imp(admin, "srv-pm-pve-01")
    assert "srv-pm-pve-02" not in {x["host"] for x in r["cut_off"] + r["down"]}
    assert r["cut_off"] == []


def test_a_gateway_down_takes_routing(admin):
    lab(admin)
    r = imp(admin, "net-fw-edge-01")
    nets = {n["network"]: n for n in r["no_routing"]}
    assert {"main", "srv", "k8s", "dmz", "iot", "guest"} == set(nets)  # mgmt's gateway is the OOB router
    assert nets["srv"]["because"] == "its gateway (net-fw-edge-01) is down"
    assert "srv-vm-mon-01" in nets["srv"]["hosts"]
    assert r["cut_off"] == []  # the switches still connect everything to the OOB router


def test_vips_and_unknown(admin):
    admin.post("/api/hosts", json={"name": "net-vip-k3s-01", "kind": "vip", "vip_members": ["srv-vm-k3s-01", "srv-vm-k3s-02"],
                                   "interfaces": [{"network": "k8s", "ip4": "10.10.128.10"}],
                                   "services": [{"name": "k3s-api", "port": 6443}]})
    r = imp(admin, "srv-vm-k3s-01")
    assert r["vips"] == [{"host": "net-vip-k3s-01", "uid": r["vips"][0]["uid"], "state": "degraded",
                          "members_affected": ["srv-vm-k3s-01"], "members_left": ["srv-vm-k3s-02"]}]
    r = imp(admin, "srv-pm-pve-01")  # both k3s nodes run there
    assert r["vips"][0]["state"] == "down" and ("net-vip-k3s-01", "k3s-api") in {(s["host"], s["name"]) for s in r["services"]}
    assert "cli-pc-admin-01" in r["unknown"]  # no cables on record: can't tell
    assert r["counts"]["vips_down"] == 1


def test_who_sees_what(admin, make_user):
    assert admin.get("/api/impact").status_code == 422
    assert admin.get("/api/impact?host=nope").get_json()["unknown_names"] == ["nope"]
    v = make_user("dc", role="viewer", scopes=["zone:DC_TC"])
    assert v.get("/api/impact?host=srv-pm-pve-01").status_code == 404  # DC_HT: not visible to them
    r = v.get("/api/impact?host=srv-vm-k3s-01").get_json()
    assert {x["host"] for x in r["down"]} == {"srv-vm-k3s-01"}
