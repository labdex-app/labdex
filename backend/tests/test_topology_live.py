"""The topology shows what's live: hosts up or down, and what switches see on each cable."""
from labdex import health
from labdex.models import Integration, db

from .test_v1200 import lk, seen


def _topo(client):
    return client.get("/api/topology").get_json()


def _cable(t, a, a_port):
    return next(e for e in t["edges"] if e["kind"] == "cable" and {(e["a_host"], e["a_port"]), (e["b_host"], e["b_port"])} & {(a, a_port)})


def test_hosts_carry_their_live_state(admin):
    i = Integration(plugin_id="victoriametrics", name="vm", enabled=True, settings={}, state={}, selectors=[])
    db.session.add(i)
    db.session.commit()
    health.report(i, {"srv-vm-dns-01": {"state": "down"}, "net-sw-core-01": {"state": "up"}})
    db.session.commit()
    nodes = {n.get("name") or n.get("host"): n for n in _topo(admin)["nodes"] if n["type"] in ("host", "box")}
    assert nodes["srv-vm-dns-01"]["live"] == "down"
    assert nodes["net-sw-core-01"]["live"] == "up"
    assert {n["live"] for k, n in nodes.items() if k not in ("srv-vm-dns-01", "net-sw-core-01")} <= {"unknown", "up", "down"}
    l3 = admin.get("/api/topology?view=l3").get_json()
    assert next(n for n in l3["nodes"] if n.get("name") == "srv-vm-dns-01")["live"] == "down"


def test_cables_say_what_the_switch_sees(admin):
    for port, peer, peer_port in [("ge-0/0/1", "srv-pm-pve-01", "eno1"), ("ge-0/0/5", "srv-vm-dns-01", "eth9"), ("ge-0/0/6", "net-fw-edge-01", "igb1")]:
        r = admin.post("/api/topology/cables", json={"a": {"host": "net-sw-core-01", "port": port}, "b": {"host": peer, "port": peer_port}})
        assert r.status_code in (200, 201), r.get_json()
    t = _topo(admin)
    assert "lldp" not in _cable(t, "net-sw-core-01", "ge-0/0/1")  # nobody reports neighbours yet: nothing to say

    sw = {"host": "net-sw-core-01"}
    seen("snmp core",
         lk("ge-0/0/1", {"name": "srv-pm-pve-01"}, "eno1", **dict(sw)),   # as on record
         lk("ge-0/0/5", {"name": "srv-pm-pve-02"}, "eno4", **dict(sw)))   # something else: a wrong patch
    t = _topo(admin)
    ok = _cable(t, "net-sw-core-01", "ge-0/0/1")["lldp"]
    assert (ok["state"], ok["sees"], ok["protocol"]) == ("seen", "srv-pm-pve-01 eno1", "lldp")
    bad = _cable(t, "net-sw-core-01", "ge-0/0/5")["lldp"]
    assert (bad["state"], bad["sees"]) == ("other", "srv-pm-pve-02 eno4")
    assert _cable(t, "net-sw-core-01", "ge-0/0/6")["lldp"]["state"] == "unseen"  # the switch reports, but not on this port

    # ignored on the Cables page: no longer a problem on the map
    key = admin.get("/api/cables/suggestions").get_json()["conflicts"][0]["key"]
    admin.post("/api/cables/suggestions/ignore", json={"keys": [key]})
    assert _cable(_topo(admin), "net-sw-core-01", "ge-0/0/5")["lldp"]["state"] == "unseen"


def test_hidden_hosts_stay_hidden_in_what_is_seen(admin, make_user):
    admin.post("/api/topology/cables", json={"a": {"host": "net-sw-core-01", "port": "ge-0/0/5"}, "b": {"host": "srv-vm-dns-01", "port": "eth9"}})
    seen("snmp core", lk("ge-0/0/5", {"name": "srv-pm-pve-02"}, "eno4", host="net-sw-core-01"))
    from labdex import cabling
    out = cabling.checks({"net-sw-core-01", "srv-vm-dns-01"})
    assert [x["sees"] for x in out.values()] == ["a host you can't see eno4"]
