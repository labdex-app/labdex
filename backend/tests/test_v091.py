"""v0.9.1: OPNsense rules from the zone matrix and services' 'allow from'."""
import pytest

from .fakes import FakeOPNsense
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


@pytest.fixture
def opn():
    o = FakeOPNsense()
    yield o
    o.srv.close()


def plan_rows(admin, job):
    return {c["data"]["key"]: c for c in admin.get(f"/api/jobs/{job['id']}").get_json()["plan"]}


def test_rules_from_policy(admin, opn):
    admin.post("/api/aliases", json={"name": "SG_WEB", "type": "port", "members": ["80", "443"]})
    admin.put("/api/zone-policy", json={"src": "CA_ST", "dst": "DC_TC", "ports": ["SG_WEB"], "description": "household to servers"})
    admin.put("/api/zone-policy", json={"src": "DC_HT", "dst": "DC_TC", "ports": ["any"]})
    h = host(admin, "srv-vm-dns-01")
    for s in h["services"]:
        s["allow_from"] = {"dns-admin": ["main"], "dns": ["CA_UT"]}[s["name"]]
    put_host(admin, "srv-vm-dns-01", services=h["services"])
    opn.rules.append({"uuid": "hand-1", "enabled": "1", "action": "pass", "direction": "in", "interface": "lan",
                      "source_net": "10.10.201.0/24", "destination_net": "10.10.100.0/24", "destination_port": "", "description": "iot to servers"})
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": opn.KEY, "api_secret": opn.SECRET})

    plan = run(admin, "fw", "sync_rules")
    rows = plan_rows(admin, plan)
    assert set(rows) == {"zone:CA_ST>DC_TC:SG_WEB", "zone:DC_HT>DC_TC", "svc:srv-vm-dns-01/dns-admin:main"}, rows.keys()
    web = rows["zone:CA_ST>DC_TC:SG_WEB"]["after"]
    assert web["interface"] == "lan" and web["protocol"] == "TCP/UDP" and web["destination_port"] == "SG_WEB"
    assert web["source_net"] == "NG_ZONE_CA_ST" and web["destination_net"] == "NG_ZONE_DC_TC"
    assert web["description"] == "CA_ST → DC_TC SG_WEB (household to servers) [zone:CA_ST>DC_TC:SG_WEB]"
    assert rows["zone:DC_HT>DC_TC"]["after"]["interface"] == "opt2" and rows["zone:DC_HT>DC_TC"]["after"]["protocol"] == "any"
    svc = rows["svc:srv-vm-dns-01/dns-admin:main"]["after"]
    assert svc["destination_port"] == "5380" and svc["protocol"] == "TCP" and svc["destination_net"].startswith("H")
    log = admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert "no interface for 'CA_UT'" in log  # the IoT VLAN isn't on this firewall's interface list

    done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert opn.filter_applied == 1 and len(opn.rules) == 4
    cat = next(iter(opn.categories))
    assert all(r.get("categories") == cat for r in opn.rules if r["uuid"] != "hand-1")
    assert run(admin, "fw", "sync_rules")["status"] == "succeeded"  # nothing to do

    # a changed cell updates its rule; a removed one deletes it; the hand-made rule stays
    admin.put("/api/zone-policy", json={"src": "DC_HT", "dst": "DC_TC", "ports": ["tcp/22"]})
    p = admin.get("/api/jobs?integration=fw&status=planned").get_json()[0]
    ops = {(c["op"], c["data"]["key"]) for c in admin.get(f"/api/jobs/{p['id']}").get_json()["plan"]}
    assert ops == {("create", "zone:DC_HT>DC_TC:tcp/22"), ("delete", "zone:DC_HT>DC_TC")}
    admin.post(f"/api/jobs/{p['id']}/approve")
    assert any(r["uuid"] == "hand-1" for r in opn.rules) and len(opn.rules) == 4

    # drift: the hand-made IoT rule isn't in the matrix
    page = admin.get("/api/integrations/fw/pages/policy").get_json()
    sec = {x["title"]: x["rows"] for x in page["sections"]}
    drift = sec["Rules no policy explains"]
    zones = {z["zone"]: z for z in sec["Zones"]}
    assert zones["DC_HT"]["state"] == "managed by labdex" and zones["DC_HT"]["labdex"] == 1
    assert zones["CA_ST"]["state"] == "not yet" and zones["CA_ST"]["other"] == 1
    assert [d["rule"] for d in drift] == ["iot to servers"] and "CA_UT → DC_TC" in drift[0]["why"]
    admin.put("/api/zone-policy", json={"src": "CA_UT", "dst": "DC_TC", "ports": ["udp/53"]})
    page = admin.get("/api/integrations/fw/pages/policy").get_json()
    assert next(x for x in page["sections"] if x["title"] == "Rules no policy explains")["rows"] == []
