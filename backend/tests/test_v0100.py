"""0.10.0: bug fixes from the first real use."""
from labdex import discovery, features
from labdex.models import DiscoveredHost, db

from .fakes import FakeTechnitium
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


def test_feature_lists_merge():
    a = {"pkgs": ["vim", "curl"], "nested": {"l": [1]}, "keep": 1}
    b = {"pkgs": ["curl", "htop"], "nested": {"l": [2]}}
    assert features.deep_merge(a, b) == {"pkgs": ["vim", "curl", "htop"], "nested": {"l": [1, 2]}, "keep": 1}
    assert features.deep_merge(a, {"pkgs": {"$replace": ["zsh"]}})["pkgs"] == ["zsh"]
    assert features.deep_merge({}, {"x": {"$replace": {"a": 1}}}) == {"x": {"a": 1}}


def test_feature_vars_merge_in_api(admin):
    admin.post("/api/features", json={"path": "base", "vars": {"pkgs": ["vim"]}})
    admin.post("/api/features", json={"path": "base.web", "vars": {"pkgs": ["nginx"]}})
    put_host(admin, "srv-vm-dns-01", features=["base.web"])
    h = host(admin, "srv-vm-dns-01")
    assert h["merged_vars"]["pkgs"] == ["vim", "nginx"], h["merged_vars"]


def test_scans_dont_learn_macs(app):
    with app.app_context():
        discovery.record_scan("nmap:scan", {"10.10.100.77"}, [{"ip": "10.10.100.77", "mac": "02:42:ac:11:00:01", "services": []}])
        discovery.record_neighbours("opnsense:fw", [{"ip": "10.10.100.78", "mac": "aa:bb:cc:dd:ee:01"}])
        db.session.commit()
        macs = {d.ip: d.mac for d in db.session.scalars(db.select(DiscoveredHost))}
        assert macs == {"10.10.100.77": "", "10.10.100.78": "aa:bb:cc:dd:ee:01"}


def test_next_free_skips_discovered(admin, app):
    first = admin.get("/api/networks/srv/next").get_json()["ip"]
    with app.app_context():
        discovery.record_neighbours("opnsense:fw", [{"ip": first, "mac": "aa:bb:cc:dd:ee:02"}])
        db.session.commit()
    assert admin.get("/api/networks/srv/next").get_json()["ip"] != first


def test_forwarder_zones_and_wildcards(admin):
    t = FakeTechnitium("lab.example.net", zones=["lab.example.net", "srv.lab.example.net"])
    t.zone_types["srv.lab.example.net"] = "Forwarder"
    try:
        h = put_host(admin, "srv-vm-dns-01", records=[{"name": "*.primary.docker", "type": "CNAME", "value": ""}])
        recs = {(r["fqdn"], r["type"]) for r in h["dns"]}
        assert ("*.primary.docker.lab.example.net", "CNAME") in recs
        r = admin.put("/api/hosts/srv-vm-dns-01", json={**h, "records": [{"name": "a.*.b", "type": "CNAME", "value": ""}]})
        assert r.status_code == 422
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": t.TOKEN})
        plan = run(admin, "dns", "sync_records")
        ch = admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]
        a = next(c for c in ch if c["name"].startswith("srv-vm-dns-01.srv.lab.example.net A"))
        assert a["data"]["zone"] == "srv.lab.example.net"  # the forwarder zone is found
        assert any(c["name"].startswith("*.primary.docker.lab.example.net CNAME") for c in ch)
    finally:
        t.srv.close()
