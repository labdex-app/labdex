"""1.9: versions, the shared topology layout, tag search, settings shown by `when`, UniFi sign-ins."""
import pytest
from packaging.version import Version as Pep440

from labdex import versioning
from labdex.models import DiscoveredHost, HostStatus, db
from labdex.plugins import coerce_fields, _validate_fields

from .fakes import FakeServer, FakeSiteManager, FakeUniFiOS
from .test_plugins import make_instance, run
from .test_v03 import host


# ------------------------------------------------------------------ versions
def test_versions_parse_order_and_move_on():
    v = versioning.parse("v1.9.0-2")
    assert (v.major, v.minor, v.sub, v.patch) == (1, 9, 0, 2) and str(v) == "1.9.0-2" and v.release == "1.9.0"
    assert v.pep440 == "1.9.0.post2" and Pep440(v.pep440) > Pep440("1.9.0") and Pep440("1.9.0-2") == Pep440("1.9.0.post2")
    assert versioning.parse("1.9.0") < versioning.parse("1.9.0-1") < versioning.parse("1.9.1") < versioning.parse("1.10.0")
    assert str(versioning.parse("1.9.0").next("patch")) == "1.9.0-1"
    assert str(v.next("sub")) == "1.9.1" and str(v.next("minor")) == "1.10.0" and str(v.next("major")) == "2.0.0"
    for bad in ("1.9", "1.9.0-rc1", "1.9.0.1", ""):
        with pytest.raises(ValueError):
            versioning.parse(bad)
    with pytest.raises(ValueError):
        v.next("micro")


def test_docker_tags_follow_the_release():
    assert versioning.docker_tags("1.9.0-1") == ["1.9.0-1", "1.9.0", "1.9", "1", "latest"]
    assert versioning.docker_tags("2.0.0") == ["2.0.0", "2.0", "2", "latest"]


def test_the_running_version_is_one():
    import labdex
    versioning.parse(labdex.__version__)


# ------------------------------------------------------------- shared layout
def test_topology_layout_is_shared_and_merged(admin, make_user):
    assert admin.get("/api/topology").get_json()["layout"] == {}
    nid = next(n["id"] for n in admin.get("/api/topology").get_json()["nodes"])
    r = admin.put("/api/topology/layout", json={"positions": {nid: {"x": 10.04, "y": "-5"}, "h:gone": {"x": 1, "y": 2}}})
    assert r.status_code == 200 and r.get_json() == {"saved": 2, "nodes": 2}
    t = admin.get("/api/topology").get_json()
    assert t["layout"] == {nid: {"x": 10.0, "y": -5.0}} and t["can_arrange"]  # only nodes still on the map
    assert t["layout_by"]
    admin.put("/api/topology/layout", json={"positions": {"h:other": {"x": 3, "y": 4}}})  # merges
    v = make_user("v", role="viewer")
    assert v.get("/api/topology").get_json()["layout"] == {nid: {"x": 10.0, "y": -5.0}}  # everyone sees the same map
    assert v.get("/api/topology").get_json()["can_arrange"] is False
    assert v.put("/api/topology/layout", json={"positions": {nid: {"x": 0, "y": 0}}}).status_code == 403
    assert v.delete("/api/topology/layout").status_code == 403
    r = admin.put("/api/topology/layout", json={"positions": {nid: {"x": 1, "y": 1}}, "replace": True})
    assert r.get_json()["nodes"] == 1
    for bad in ({"positions": {nid: {"x": "a", "y": 1}}}, {"positions": {nid: 5}}, {"positions": [1]}):
        assert admin.put("/api/topology/layout", json=bad).status_code == 422
    assert admin.delete("/api/topology/layout").status_code == 204
    assert admin.get("/api/topology").get_json()["layout"] == {}


# -------------------------------------------------------------------- tags
def test_search_offers_hosts_by_tag(admin):
    h = host(admin, "srv-vm-dns-01")
    assert admin.put("/api/hosts/srv-vm-dns-01", json={**h, "tags": ["backup"]}).status_code == 200
    r = admin.get("/api/search?q=tag:back").get_json()["results"]
    assert r and all(x["kind"] == "filter" for x in r)
    assert r[0] == {"kind": "filter", "title": "Hosts tagged backup", "sub": "1 host", "link": "/hosts?q=tag:backup"}
    plain = admin.get("/api/search?q=backup").get_json()["results"]
    assert plain[0]["kind"] == "filter"  # a word that is a tag offers the filter first
    assert not any(x["kind"] == "filter" for x in admin.get("/api/search?q=back").get_json()["results"])  # but not a part of one


# --------------------------------------------------------- settings `when`
FIELDS = [{"key": "auth", "type": "select", "default": "key", "options": ["key", "password"]},
          {"key": "api_key", "type": "secret", "required": True, "when": {"auth": "key"}},
          {"key": "username", "type": "string", "required": True, "when": {"auth": ["password"]}}]


def test_hidden_settings_are_not_required_and_kept(app):
    vault = None  # no secret gets encrypted here
    with app.app_context():
        out, errs = coerce_fields(FIELDS, {"auth": "password", "username": "me"}, {"api_key": "enc"}, vault)
        assert not errs and out == {"auth": "password", "username": "me", "api_key": "enc"}
        out, errs = coerce_fields(FIELDS, {"auth": "password"}, {}, vault)
        assert errs == [{"field": "username", "message": "required"}]
        out, errs = coerce_fields(FIELDS, {}, {}, vault)
        assert errs == [{"field": "api_key", "message": "required"}]
    assert "when names settings above it" in _validate_fields([{"key": "a", "when": {"b": "x"}}, {"key": "b"}], "settings")


# --------------------------------------------------------------- UniFi
def _unifi_checks(admin, fake, job):
    assert {k: v for k, v in job["result"].items() if k != "links"} == {"clients": 4, "recorded": 3, "devices": 2, "matched": 1}, job  # links: 1.20, local accounts only
    d = db.session.scalar(db.select(DiscoveredHost).where(DiscoveredHost.ip == "172.16.0.6"))
    assert d.mac == "aa:bb:cc:00:00:02" and d.hostname == "tv"  # the name given in UniFi wins over the DHCP one
    st = db.session.scalar(db.select(HostStatus))
    assert st.host == "net-sw-core-01" and st.state == "up" and "USW-Pro-24" in st.detail
    assert not fake.sessions and fake.logged_out == 2  # signed out after the check and the poll


@pytest.mark.parametrize("classic", [False, True], ids=["unifi-os", "network-app"])
def test_unifi_local_account(admin, classic):
    fake = FakeUniFiOS(classic=classic)
    srv = FakeServer(fake)
    try:
        auth = "classic" if classic else "password"
        make_instance(admin, "unifi", "unifi", {"auth": auth, "url": srv.url, "username": "viewer", "password": "pw"})
        assert run(admin, "unifi", "test")["result"]["message"] == "2 site(s): Default, Garage"
        _unifi_checks(admin, fake, run(admin, "unifi", "poll"))
    finally:
        srv.close()


def test_unifi_local_account_errors_say_what_to_do(admin):
    fake = FakeUniFiOS()
    srv = FakeServer(fake)
    try:
        make_instance(admin, "unifi", "unifi", {"auth": "password", "url": srv.url, "username": "owner", "password": "pw"})
        job = run(admin, "unifi", "test")
        assert job["status"] == "failed" and "two-factor" in job["log"], job
        r = admin.put("/api/integrations/unifi", json={"settings": {"auth": "password", "url": srv.url, "username": "viewer", "password": "nope"}})
        assert r.status_code == 200, r.get_json()
        job = run(admin, "unifi", "test")
        assert job["status"] == "failed" and "username or password" in job["log"], job
    finally:
        srv.close()


def test_unifi_needs_only_what_its_sign_in_uses(admin):
    make_instance(admin, "unifi", "u2", {"auth": "cloud", "api_key": "k"})  # no URL, username or password for Site Manager
    admin.post("/api/integrations", json={"plugin_id": "unifi", "name": "u3"})
    r = admin.put("/api/integrations/u3", json={"settings": {"auth": "password", "url": "https://x"}, "enabled": True})
    assert r.status_code == 422 and {e["field"] for e in r.get_json()["errors"]} == {"username", "password"}, r.get_json()


def test_unifi_site_manager(admin):
    fake = FakeSiteManager()
    home = fake.consoles["c-home"]
    home.clients = [{"name": "phone", "ipAddress": "172.16.1.1", "macAddress": "AA:BB:CC:00:09:01"}]
    home.devices = [{"name": "core", "model": "USW-Pro-24", "ipAddress": "10.10.3.2", "macAddress": "aa:aa:aa:aa:aa:01", "state": "ONLINE"}]
    srv = FakeServer(fake)
    try:
        make_instance(admin, "unifi", "unifi", {"auth": "cloud", "api_key": "cloudkey", "cloud_url": srv.url})
        job = run(admin, "unifi", "poll")
        # Home UDM through the connector; the shed console can't be reached, so its devices come from Site Manager
        assert job["result"] == {"clients": 1, "recorded": 1, "devices": 2, "matched": 1, "unreachable": 1}, job
        assert "Shed UCG" in job["log"]
        w = admin.get("/api/widgets").get_json()
        assert "shed-sw" in str(w) and "core" in str(w), w
        admin.put("/api/integrations/unifi", json={"settings": {"auth": "cloud", "api_key": "", "cloud_url": srv.url, "consoles": ["Home UDM"]}})
        assert run(admin, "unifi", "test")["result"]["message"] == "1 site(s): Default"
        admin.put("/api/integrations/unifi", json={"settings": {"auth": "cloud", "api_key": "wrong", "cloud_url": srv.url, "consoles": []}})
        assert run(admin, "unifi", "test")["status"] == "failed"
    finally:
        srv.close()
