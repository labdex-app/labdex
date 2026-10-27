"""1.6: one search for the command palette; changes to many hosts at once."""
from .test_plugins import make_instance
from .test_v03 import host



def host_uid(c, name):
    return c.get(f"/api/hosts/{name}").get_json()["uid"]

def search(c, q):
    return c.get(f"/api/search?q={q}").get_json()["results"]


def test_search_hosts_by_name_address_and_mac(admin):
    r = search(admin, "srv-vm-dns")
    assert r[0]["kind"] == "host" and r[0]["title"] == "srv-vm-dns-01" and r[0]["link"] == f"/hosts/{host_uid(admin, 'srv-vm-dns-01')}"
    r = search(admin, "10.10.100.53")
    assert r[0]["title"] == "srv-vm-dns-01" and r[0]["sub"].startswith("10.10.100.53")
    net = next(x for x in r if x["kind"] == "network")
    assert net["title"] == "srv" and "10.10.100.53 is in 10.10.100.0/24" in net["sub"]
    h = host(admin, "srv-vm-dns-01")
    h["interfaces"][0]["mac"] = "aa:bb:cc:11:22:33"
    assert admin.put("/api/hosts/srv-vm-dns-01", json=h).status_code == 200
    for q in ("AA-BB-CC-11-22-33", "aabb.cc11.2233", "aa:bb:cc"):
        assert search(admin, q)[0]["title"] == "srv-vm-dns-01", q
    v = search(admin, "vlan 100")[0]
    assert v["kind"] == "network" and v["title"] == "srv" and v["sub"].startswith("VLAN 100") and v["link"] == f"/networks/{admin.get('/api/networks/srv').get_json()['uid']}"
    assert search(admin, "x") == []  # too short


def test_search_services_aliases_actions_and_rights(admin, make_user):
    make_instance(admin, "exports", "files", {})
    kinds = {r["kind"] for r in search(admin, "files")}
    assert "integration" in kinds
    r = search(admin, "N_ZONE_DC_TC_SRV")
    assert any(x["kind"] == "alias" and x["title"] == "N_ZONE_DC_TC_SRV" for x in r)
    # someone who only sees CA_* doesn't find DC hosts or networks
    v = make_user("v", role="viewer", scopes=["zone:CA_*"])
    assert not any(x["title"] == "srv-vm-dns-01" for x in search(v, "srv-vm-dns"))
    assert not any(x["kind"] == "action" for x in search(v, "files"))


def test_bulk_tags_status_runs_on_and_module(admin):
    make_instance(admin, "exports", "files", {})
    names = ["srv-vm-dns-01", "srv-vm-mon-01"]
    r = admin.post("/api/hosts/bulk", json={"names": names, "add_tags": ["backup", "Nightly"], "status": "offline", "runs_on": "srv-pm-pve-01"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["summary"] == "bulk (2 hosts): status offline, runs on srv-pm-pve-01, tags +backup +nightly"
    for n in names:
        h = host(admin, n)
        assert {"backup", "nightly"} <= set(h["tags"]) and h["status"] == "offline" and h["runs_on"] == "srv-pm-pve-01"
    r = admin.post("/api/hosts/bulk", json={"names": names, "remove_tags": ["nightly"], "module": {"integration": "files", "enabled": False}})
    assert r.status_code == 200
    assert "nightly" not in host(admin, names[0])["tags"]
    hist = admin.get(f"/api/history?object_name={names[1]}").get_json()
    assert hist[0]["summary"].startswith("bulk (2 hosts): tags −nightly, files off")


def test_bulk_is_all_or_nothing(admin, make_user):
    r = admin.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01", "nope"], "status": "offline"})
    assert r.status_code == 404
    r = admin.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01", "srv-vm-mon-01"], "runs_on": "no-such-host"})
    assert r.status_code == 422 and "nothing changed" in r.get_json()["error"]
    assert host(admin, "srv-vm-dns-01")["runs_on"] != "no-such-host"
    # editing needs edit rights on every host
    op = make_user("o", role="operator", scopes=["zone:CA_*"])
    assert op.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01"], "status": "offline"}).status_code in (403, 404)
    assert admin.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01"]}).status_code == 422  # nothing to change


def test_bulk_delete(admin):
    r = admin.post("/api/hosts/bulk", json={"names": ["srv-vm-dns-01", "srv-vm-mon-01"], "delete": True})
    assert r.status_code == 200
    assert admin.get("/api/hosts/srv-vm-dns-01").status_code == 404
