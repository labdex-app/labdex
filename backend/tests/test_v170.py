"""1.7: address map, internal cables and the topology, plans applied in part."""
from labdex.plugins import installer

from .test_plugins import make_instance, run, write_plugin
from .test_v03 import host


def put(admin, name, **changes):
    h = host(admin, name)
    r = admin.put(f"/api/hosts/{name}", json={**h, **changes})
    return r


# ------------------------------------------------------------------ address map
def test_address_map_states(admin):
    from labdex.models import DiscoveredHost, db
    db.session.add(DiscoveredHost(ip="10.10.100.77", mac="de:ad:be:ef:00:01", hostname="mystery", source="arp"))
    h = host(admin, "srv-vm-dns-01")
    db.session.add(DiscoveredHost(ip=h["interfaces"][0]["ip4"], mac="de:ad:be:ef:00:02", hostname="dns", source="arp"))
    h["interfaces"][0]["mac"] = "aa:aa:aa:aa:aa:aa"
    assert admin.put("/api/hosts/srv-vm-dns-01", json=h).status_code == 200
    db.session.commit()
    d = admin.get("/api/networks/srv/map").get_json()
    assert d["prefix"] == "10.10.100.0/24" and d["blocks"] == 1 and len(d["cells"]) == 256
    cells = {c["ip"]: c for c in d["cells"]}
    assert cells["10.10.100.0"]["state"] == "network" and cells["10.10.100.255"]["state"] == "broadcast"
    assert cells["10.10.100.77"]["state"] == "seen" and cells["10.10.100.77"]["seen"]["hostname"] == "mystery"
    assert cells[h["interfaces"][0]["ip4"]]["state"] == "conflict"  # Discovery saw another MAC there
    assert cells["10.10.100.254"]["gateway"]
    assert cells["10.10.100.10"]["state"] in ("free", "host")
    assert sum(d["counts"].values()) == 256
    assert admin.get("/api/networks/tailnet/map").status_code == 200  # a /10: paged by /24 blocks
    big = admin.get("/api/networks/tailnet/map?block=3").get_json()
    assert big["blocks"] == 256 and big["block"] == 3 and big["range"] == ["100.64.3.0", "100.64.3.255"]


# --------------------------------------------------------- internal cables
def test_internal_cables_only_between_guest_and_its_host(admin):
    pve = host(admin, "srv-pm-pve-01")
    assert any(i["label"] for i in pve["interfaces"])
    r = put(admin, "srv-vm-dns-01", runs_on="srv-pm-pve-01",
            cables=[{"port": "eth0", "peer": "srv-pm-pve-01", "peer_port": "vmbr0", "medium": "internal"}])
    assert r.status_code == 200, r.get_json()
    assert host(admin, "srv-vm-dns-01")["cables"][0]["medium"] == "virtual"
    assert any(c["peer"] == "srv-vm-dns-01" for c in host(admin, "srv-pm-pve-01")["cables"])  # shows on both
    r = put(admin, "srv-vm-mon-01", runs_on="", cables=[{"port": "eth0", "peer": "srv-pm-pve-01", "peer_port": "vmbr0", "medium": "virtual"}])
    assert r.status_code == 422 and "internal cable" in str(r.get_json())
    # two guests of the same host may be joined internally
    put(admin, "srv-vm-mon-01", runs_on="srv-pm-pve-01")
    r = put(admin, "srv-vm-mon-01", cables=[{"port": "eth9", "peer": "srv-vm-dns-01", "peer_port": "eth9", "medium": "virtual"}])
    assert r.status_code == 200, r.get_json()


def test_topology_boxes_guests_and_links(admin):
    put(admin, "srv-vm-dns-01", runs_on="srv-pm-pve-01",
        cables=[{"port": "eth0", "peer": "srv-pm-pve-01", "peer_port": "vmbr0", "medium": "internal"}])
    put(admin, "srv-vm-mon-01", runs_on="srv-pm-pve-01")
    t = admin.get("/api/topology").get_json()
    nodes = {n["id"]: n for n in t["nodes"]}
    assert nodes["box:srv-pm-pve-01"]["type"] == "box"
    assert nodes["h:srv-vm-dns-01"]["parent"] == "box:srv-pm-pve-01" and nodes["h:srv-vm-mon-01"]["parent"] == "box:srv-pm-pve-01"
    assert "h:srv-pm-pve-01" not in nodes  # the hypervisor is its box
    assert nodes["p:srv-pm-pve-01:vmbr0"]["parent"] == "box:srv-pm-pve-01"
    internal = [e for e in t["edges"] if e["kind"] == "internal"]
    assert any(e["source"] == "h:srv-vm-dns-01" and e["target"] == "p:srv-pm-pve-01:vmbr0" for e in internal)
    # a zone narrows it to that zone's hosts and what they're cabled to
    z = admin.get(f"/api/topology?zone={nodes['h:srv-vm-dns-01']['zone']}").get_json()
    assert any(n["id"] == "h:srv-vm-dns-01" for n in z["nodes"])


def test_topology_respects_rights(admin, make_user):
    put(admin, "srv-vm-dns-01", runs_on="srv-pm-pve-01")
    v = make_user("v", role="viewer", scopes=["zone:CA_*"])
    t = v.get("/api/topology").get_json()
    assert not any(n.get("name") == "srv-vm-dns-01" or n.get("host") == "srv-pm-pve-01" for n in t["nodes"])


# ------------------------------------------------------------ partial apply
PLANNER = """
id: planner
name: Planner
kind: integration
actions:
  - {id: tidy, mode: plan}
"""
PLANNER_PY = """
from labdex.plugins.base import Change, Plugin as Base
class Plugin(Base):
    def plan_tidy(self, params):
        return [Change("create", "record", n, f"add {n}", after={"name": n}) for n in ("a", "b", "c")] + \\
               [Change("update", "alias", "x", "x changes", before={"members": ["1"], "desc": "same"}, after={"members": ["1", "2"], "desc": "same"})]
    def apply_tidy(self, changes, params):
        return {"applied": [c.name for c in changes]}
"""


def test_untick_changes_at_approval(admin, loaded):
    write_plugin(loaded, "planner", PLANNER, PLANNER_PY)
    installer.reload_registry(loaded)
    make_instance(admin, "planner", "p", {})
    job = run(admin, "p", "tidy")
    r = admin.post(f"/api/jobs/{job['id']}/approve", json={"skip": [1, 3]})
    assert r.status_code == 202, r.get_json()
    d = admin.get(f"/api/jobs/{job['id']}").get_json()
    assert d["status"] == "applied" and d["result"] == {"applied": ["a", "c"]}
    assert [c.get("skipped") for c in d["plan"]] == [False, True, False, True]
    assert "left out 2 change(s): b, x" in d["log"]
    hist = admin.get("/api/history?object_name=p").get_json()
    assert any("2 change(s)" in e["summary"] for e in hist)


def test_untick_refuses_nonsense(admin, loaded):
    write_plugin(loaded, "planner", PLANNER, PLANNER_PY)
    installer.reload_registry(loaded)
    make_instance(admin, "planner", "p", {})
    job = run(admin, "p", "tidy")
    for skip, msg in (([0, 1, 2, 3], "discard the plan instead"), ([9], "isn't in the plan"), (["x"], "positions")):
        r = admin.post(f"/api/jobs/{job['id']}/approve", json={"skip": skip})
        assert r.status_code in (409, 422) and msg in str(r.get_json()), (skip, r.get_json())
    assert admin.get(f"/api/jobs/{job['id']}").get_json()["status"] == "planned"


def test_a_bridge_holds_many_internal_links(admin):
    for g in ("srv-vm-dns-01", "srv-vm-mon-01"):
        r = put(admin, g, runs_on="srv-pm-pve-01", cables=[{"port": "eth0", "peer": "srv-pm-pve-01", "peer_port": "vmbr0", "medium": "internal"}])
        assert r.status_code == 200, r.get_json()
    pve = host(admin, "srv-pm-pve-01")
    assert sorted(c["peer"] for c in pve["cables"] if c["port"] == "vmbr0") == ["srv-vm-dns-01", "srv-vm-mon-01"]
    # and the hypervisor saves with both on its bridge
    assert admin.put("/api/hosts/srv-pm-pve-01", json=pve).status_code == 200
    assert len([c for c in host(admin, "srv-pm-pve-01")["cables"] if c["port"] == "vmbr0"]) == 2
    t = admin.get("/api/topology").get_json()
    assert len([e for e in t["edges"] if e["kind"] == "internal" and e["target"] == "p:srv-pm-pve-01:vmbr0"]) == 2
