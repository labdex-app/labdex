"""v0.9.2: public services: port forwards and split-horizon DNS."""
import pytest

from .fakes import FakeOPNsense, FakeTechnitium
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


@pytest.fixture
def opn():
    o = FakeOPNsense()
    yield o
    o.srv.close()


def make_public(admin, name="srv-vm-dns-01", svc="dns-admin", **kw):
    h = host(admin, name)
    for s in h["services"]:
        if s["name"] == svc:
            s.update({"public": True, "public_name": "dns.example.com", "public_port": 8443} | kw)
    return put_host(admin, name, services=h["services"])


def test_public_service_validation_and_lint(admin):
    h = host(admin, "srv-vm-dns-01")
    h["services"][0].update(public=True, public_name="not a name", public_port=70000)
    r = admin.put("/api/hosts/srv-vm-dns-01", json=h)
    assert r.status_code == 422
    fields = {e["field"] for e in r.get_json()["errors"]}
    assert {"services[0].public_name", "services[0].public_port"} <= fields
    make_public(admin)
    lint = {(i["code"], i["ref"]) for i in admin.get("/api/lint").get_json()["issues"]}
    assert ("public-outside-dmz", "srv-vm-dns-01") in lint
    svc = next(s for s in host(admin, "srv-vm-dns-01")["services"] if s["name"] == "dns-admin")
    assert svc["public"] and svc["public_port"] == 8443 and svc["public_name"] == "dns.example.com"


def test_port_forwards(admin, opn):
    make_public(admin)
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": opn.KEY, "api_secret": opn.SECRET})
    plan = run(admin, "fw", "sync_nat")
    ch = admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]
    assert len(ch) == 1 and ch[0]["op"] == "create"
    w = ch[0]["after"]
    assert w["destination"] == {"network": "wanip", "port": "8443"} and w["target"] == "10.10.100.53" and w["local-port"] == "5380"
    assert w["protocol"] == "TCP" and w["descr"].endswith("[nat:srv-vm-dns-01/dns-admin]")
    done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
    assert done["status"] == "applied" and opn.nat_applied == 1 and opn.nat[0]["category"] == "automation:labdex"
    assert run(admin, "fw", "sync_nat")["status"] == "succeeded"
    # the WAN port changes; then the service isn't public any more
    make_public(admin, public_port=9443)
    p = admin.get("/api/jobs?integration=fw&status=planned").get_json()
    p = next(j for j in p if j["action"] == "sync_nat")
    assert admin.get(f"/api/jobs/{p['id']}").get_json()["plan"][0]["op"] == "update"
    admin.post(f"/api/jobs/{p['id']}/approve")
    assert opn.nat[0]["destination"]["port"] == "9443"
    make_public(admin, public=False)
    p = next(j for j in admin.get("/api/jobs?integration=fw&status=planned").get_json() if j["action"] == "sync_nat")
    admin.post(f"/api/jobs/{p['id']}/approve")
    assert opn.nat == []


def test_split_horizon(admin):
    make_public(admin)
    t = FakeTechnitium("lab.example.net", zones=["lab.example.net"])
    try:
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": t.TOKEN})
        plan = run(admin, "dns", "sync_records")
        ch = admin.get(f"/api/jobs/{plan['id']}").get_json()["plan"]
        zone = [c for c in ch if c["kind"] == "zone"]
        assert [c["name"] for c in zone] == ["dns.example.com"]
        pub = [c for c in ch if c["name"].startswith("dns.example.com ")]
        assert {c["data"]["type"] for c in pub} == {"A", "AAAA"} and all(c["data"]["zone"] == "dns.example.com" for c in pub)
        done = admin.post(f"/api/jobs/{plan['id']}/approve").get_json()
        assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
        assert t.created_zones == ["dns.example.com"]
        assert any(r["name"] == "dns.example.com" and r["rData"]["ipAddress"] == "10.10.100.53" for r in t.records)
        assert run(admin, "dns", "sync_records")["status"] == "succeeded"
    finally:
        t.srv.close()
