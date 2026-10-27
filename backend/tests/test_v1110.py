"""1.11: stable ids for hosts, networks and zones; addresses per family; the zone page."""
from labdex.models import UID_ALPHABET

from .test_v03 import host


def test_hosts_have_a_stable_id(admin):
    h = host(admin, "srv-vm-dns-01")
    uid = h["uid"]
    assert len(uid) == 10 and set(uid) <= set(UID_ALPHABET)
    assert admin.get(f"/api/hosts/{uid}").get_json()["name"] == "srv-vm-dns-01"  # by id
    assert any(x["uid"] == uid for x in admin.get("/api/hosts").get_json())
    h["name"] = "srv-vm-resolver-01"
    r = admin.put(f"/api/hosts/{uid}", json=h)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["uid"] == uid and admin.get(f"/api/hosts/{uid}").get_json()["name"] == "srv-vm-resolver-01"
    hist = admin.get("/api/history?object_name=srv-vm-resolver-01").get_json()
    assert "uid" not in hist[0]["summary"]  # the id never shows as a change
    assert admin.post("/api/hosts", json={"name": "srv-vm-new-01", "zone": "DC_TC"}).get_json()["uid"] != uid


def test_networks_and_zones_have_a_stable_id(admin):
    nets = admin.get("/api/networks").get_json()
    srv = next(n for n in nets if n["name"] == "srv")
    assert admin.get(f"/api/networks/{srv['uid']}").get_json()["name"] == "srv"
    zones = admin.get("/api/zones").get_json()["zones"]
    tc = next(z for z in zones if z["code"] == "DC_TC")
    assert any(z["uid"] == tc["uid"] for z in admin.get("/api/meta").get_json()["zones"])
    d = admin.get(f"/api/zones/zones/{tc['uid']}").get_json()
    assert d["code"] == "DC_TC" and any(n["name"] == "srv" for n in d["networks"]) and d["hosts"]
    assert admin.get("/api/zones/zones/dc_tc").get_json()["uid"] == tc["uid"]  # by code too
    assert admin.get("/api/zones/zones/nope").status_code == 404
    # a renamed zone keeps its id, and so does a zone whose area is renamed
    r = admin.put("/api/zones/zones/DC_TC", json={"code": "DC_TRUSTED", "area": "DC", "name": "Trusted", "rank": 4})
    assert r.status_code == 200, r.get_json()
    assert admin.get(f"/api/zones/zones/{tc['uid']}").get_json()["code"] == "DC_TRUSTED"
    r = admin.put("/api/zones/areas/DC", json={"code": "DATA", "name": "Datacenter"})
    assert r.status_code == 200, r.get_json()
    assert admin.get(f"/api/zones/zones/{tc['uid']}").get_json()["code"] == "DATA_TRUSTED"


def test_zone_page_respects_rights(admin, make_user):
    tc = next(z for z in admin.get("/api/zones").get_json()["zones"] if z["code"] == "DC_TC")
    v = make_user("v", role="viewer", scopes=["zone:CA_*"])
    d = v.get(f"/api/zones/zones/{tc['uid']}").get_json()
    visible = {h["name"] for h in v.get("/api/hosts").get_json()}
    assert d["networks"] == [] and {h["name"] for h in d["hosts"]} <= visible and d["can_edit"] is False
    assert len(d["hosts"]) < len(admin.get(f"/api/zones/zones/{tc['uid']}").get_json()["hosts"])


def test_secondary_addresses_both_families(admin):
    h = host(admin, "srv-vm-dns-01")
    i = h["interfaces"][0]
    i["extra_ips"] = ["10.10.100.54", "fd00:10:10:100::54"]
    assert admin.put("/api/hosts/srv-vm-dns-01", json=h).status_code == 200
    assert host(admin, "srv-vm-dns-01")["interfaces"][0]["extra_ips"] == ["10.10.100.54", "fd00:10:10:100::54"]
    t = admin.get("/api/topology?view=l3").get_json()
    e = next(e for e in t["edges"] if e["source"] == "h:srv-vm-dns-01" and e["target"] == "n:srv")
    assert "10.10.100.54" in e["label"]
    i["extra_ips"] = ["10.99.0.1"]
    r = admin.put("/api/hosts/srv-vm-dns-01", json=h)
    assert r.status_code == 422 and "10.99.0.1 is outside srv" in str(r.get_json())


def test_a_new_event_stream_replaces_the_oldest(admin):
    """Reloading a page quickly leaves streams of closed tabs open for a while: the newest wins."""
    import threading
    from labdex.api import events_api
    from labdex.models import User, db
    uid = db.session.scalar(db.select(User.id).where(User.is_admin.is_(True)))
    old = [threading.Event() for _ in range(events_api.MAX_STREAMS)]
    events_api._open[uid] = list(old)
    r = admin.get("/api/events?once=1")
    assert r.status_code == 200
    r.get_data()  # run the stream to its end
    assert old[0].is_set() and not old[1].is_set()
    assert len(events_api._open[uid]) == events_api.MAX_STREAMS - 1  # the finished one left
    events_api._open.pop(uid, None)
