"""The Pulumi engine plugin: resources made from the public site, plans from previews, Contabo's API.

Pulumi itself (the SDK and the CLI) isn't needed: the engine's preview and up are replaced by
fakes, and Contabo by a fake session. A real run is covered by the opt-in test at the end."""
import os
import shutil

import pytest

from labdex.iac import Ref, Resource, check_unique, contabo, engine, public
from labdex.models import Integration, db

from .test_sites import add_public


def host(name, fun="", ip4="", ip6="", **ms):
    ifs = [{"network": "contabo", "ip4": ip4, "ip6": ip6, "ip6_all": [ip6] if ip6 else [], "primary": True}] if (ip4 or ip6) else []
    return {"name": name, "fun": fun, "kind": "device", "interfaces": ifs, "module_settings": ms}


CF = {"cloudflare": True, "cloudflare_zone": "example.com", "cloudflare_zone_id": "z1", "record_name": "{fun}.node"}
CT = {"contabo": True, "default_product_id": "V45", "default_image_id": "img-1", "default_region": "EU", "ssh_keys": "11 12"}


# ----------------------------------------------------------------- resources
def test_records_per_host():
    res, notes = public.resources([host("srv-vm-oak-01", "everest", "203.0.113.11", "2001:db8::11"),
                                   host("srv-vm-oak-02", "", "203.0.113.12")], {**CF, "ipv6": True})
    names = [(r.type, r.name) for r in res]
    assert names == [("cloudflare:dns", "everest.node.example.com/A"), ("cloudflare:dns", "everest.node.example.com/AAAA"),
                     ("cloudflare:dns", "srv-vm-oak-02.node.example.com/A")]
    assert res[1].props["content"] == "2001:db8::11"
    a = res[0].props
    assert a == {"zone_id": "z1", "name": "everest.node.example.com", "type": "A", "content": "203.0.113.11", "ttl": 1,
                 "proxied": False, "comment": "labdex"}
    assert notes == []


def test_private_addresses_get_no_record():
    res, notes = public.resources([host("srv-vm-x-01", "x", "10.0.0.5")], CF)
    assert res == [] and "no public IPv4" in notes[0]


def test_instances_new_and_adopted():
    hosts = [host("srv-vm-oak-01", "everest", "", instance_id="2001"), host("srv-vm-oak-02", "k2"),
             host("srv-vm-oak-03", "", no_instance=True), host("srv-vm-oak-04", "", product_id="V91", region="US-east")]
    res, notes = public.resources(hosts, {**CT, **CF})
    inst = {r.name: r for r in res if r.type == "contabo:instance"}
    assert set(inst) == {"srv-vm-oak-01", "srv-vm-oak-02", "srv-vm-oak-04"}
    adopted = inst["srv-vm-oak-01"]
    assert adopted.import_id == "2001" and adopted.protect and "image_id" not in adopted.props and "ssh_keys" not in adopted.props
    new = inst["srv-vm-oak-02"].props
    assert new["product_id"] == "V45" and new["image_id"] == "img-1" and new["ssh_keys"] == ["11", "12"] and not new["allow_cancel"]
    assert inst["srv-vm-oak-04"].props["region"] == "US-east" and inst["srv-vm-oak-04"].props["product_id"] == "V91"
    # a record for a node without an address yet points at the instance's output
    rec = next(r for r in res if r.name == "k2.node.example.com/A")
    assert rec.props["content"] == Ref("srv-vm-oak-02", "ip_v4") and rec.depends_on == ["srv-vm-oak-02"]
    assert rec.as_dict()["props"]["content"] == {"$ref": "srv-vm-oak-02.ip_v4"}
    assert check_unique(res) == []


def test_adopted_record_and_skips():
    res, _ = public.resources([host("a", "a", "203.0.113.1", record_id_a="r9"), host("b", "b", "203.0.113.2", skip=True),
                               {**host("v", "v", "203.0.113.3"), "kind": "vip"}], CF)
    assert [(r.name, r.import_id) for r in res] == [("a.node.example.com/A", "z1/r9")]


def test_duplicates_are_found():
    assert check_unique([Resource("cloudflare:dns", "x/A"), Resource("cloudflare:dns", "x/A")]) == ["x/A"]


# --------------------------------------------------------------------- plans
def test_steps_to_changes():
    steps = [engine.Step("create", "cloudflare:index/dnsRecord:DnsRecord", "a.example.com/A", new={"inputs": {"content": "203.0.113.1", "apiToken": "x"}}),
             engine.Step("same", "cloudflare:index/dnsRecord:DnsRecord", "b"),
             engine.Step("update", "pulumi-python:dynamic:Resource", "srv-vm-oak-01", diffs=["display_name"],
                         old={"inputs": {"display_name": "old", "__provider": "pickled"}}, new={"inputs": {"display_name": "new"}}),
             engine.Step("replace", "pulumi-python:dynamic:Resource", "srv-vm-oak-02"),
             engine.Step("delete-replaced", "pulumi-python:dynamic:Resource", "srv-vm-oak-02"),
             engine.Step("delete", "cloudflare:index/dnsRecord:DnsRecord", "gone/A"),
             engine.Step("import", "pulumi-python:dynamic:Resource", "srv-vm-oak-03")]
    ch = engine.steps_to_changes(steps)
    assert [(c["op"], c["name"]) for c in ch] == [("create", "a.example.com/A"), ("update", "srv-vm-oak-01"), ("update", "srv-vm-oak-02"),
                                                 ("delete", "gone/A"), ("other", "srv-vm-oak-03")]
    assert ch[0]["kind"] == "cloudflare DnsRecord" and ch[0]["after"] == {"content": "203.0.113.1"}       # no token
    assert ch[1]["summary"] == "contabo instance srv-vm-oak-01: changes display_name" and "__provider" not in ch[1]["before"]
    assert "replaced" in ch[2]["summary"] and "adopted" in ch[4]["summary"]


def test_engine_needs_a_passphrase(tmp_path):
    with pytest.raises(Exception, match="passphrase"):
        engine.Engine(project="p", stack="s", state_dir=tmp_path, passphrase="")


# ------------------------------------------------------------------- contabo
class FakeResponse:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text
        self.content = b"x" if data is not None else b""

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.calls = []
        self.instances = {"2001": {"instanceId": 2001, "displayName": "srv-vm-oak-01", "productId": "V45", "region": "EU", "status": "running",
                                   "imageId": "img-1", "ipConfig": {"v4": {"ip": "203.0.113.11"}, "v6": {"ip": "2001:db8::11"}}}}

    def post(self, url, data=None, timeout=None):
        self.calls.append(("AUTH", url, data))
        ok = data["client_secret"] == "s"
        return FakeResponse(200 if ok else 401, {"access_token": "t"} if ok else None)

    def request(self, method, url, json=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer t" and headers["x-request-id"]
        path = url.split("/v1", 1)[1]
        self.calls.append((method, path, json))
        if method == "GET" and path.startswith("/compute/instances?"):
            return FakeResponse(200, {"data": list(self.instances.values()), "_pagination": {"totalPages": 1}})
        if method == "GET":
            i = self.instances.get(path.rsplit("/", 1)[1])
            return FakeResponse(200, {"data": [i]}) if i else FakeResponse(404, text="not found")
        if method == "POST" and path == "/compute/instances":
            row = {"instanceId": 2002, "displayName": json["displayName"], "productId": json["productId"], "region": json["region"], "status": "provisioning"}
            self.instances["2002"] = row
            return FakeResponse(201, {"data": [row]})
        return FakeResponse(200, {})


def test_contabo_client():
    s = FakeSession()
    c = contabo.Client("id", "s", "u@example.com", "p", session=s)
    assert [i["instanceId"] for i in c.instances()] == [2001]
    assert c.instance("2001")["displayName"] == "srv-vm-oak-01" and c.instance("9") is None
    row = c.create({"image_id": "img-1", "product_id": "V45", "region": "EU", "display_name": "srv-vm-oak-02", "ssh_keys": ["11"]})
    assert row["instanceId"] == 2002
    assert ("POST", "/compute/instances", {"imageId": "img-1", "productId": "V45", "region": "EU", "displayName": "srv-vm-oak-02",
                                          "period": 1, "sshKeys": [11]}) in s.calls
    c.rename(2001, "x")
    c.cancel(2001)
    assert ("PATCH", "/compute/instances/2001", {"displayName": "x"}) in s.calls and ("POST", "/compute/instances/2001/cancel", {}) in s.calls
    assert sum(1 for x in s.calls if x[0] == "AUTH") == 1                                   # one token for the session
    out = contabo.outputs(s.instances["2001"], {"display_name": "srv-vm-oak-01"})
    assert out["ip_v4"] == "203.0.113.11" and out["instance_id"] == "2001"
    with pytest.raises(contabo.ContaboError, match="sign-in failed"):
        contabo.Client("id", "bad", "u", "p", session=FakeSession()).instances()


def test_contabo_diff():
    old = {"display_name": "a", "product_id": "V45", "region": "EU", "image_id": "img-1"}
    assert contabo.diff(old, {**old, "display_name": "b"}) == (["display_name"], [])
    assert contabo.diff(old, {**old, "product_id": "V91"}) == (["product_id"], ["product_id"])
    assert contabo.diff(old, {**old, "image_id": "img-2"})[1] == ["image_id"]                # a reinstall wipes the disk
    adopted = {"display_name": "a", "product_id": "V45", "region": "EU"}
    assert contabo.diff(adopted, {**adopted, "image_id": "img-2"})[1] == []                 # not managed before: no replace
    assert contabo.diff(old, {"display_name": "a"}) == ([], [])


# ----------------------------------------------------------------- the plugin
@pytest.fixture
def public_site(admin):
    add_public(admin)
    r = admin.post("/api/integrations", json={"plugin_id": "pulumi", "name": "public-iac", "site": "public"})
    assert r.status_code == 201, r.get_json()
    r = admin.put("/api/integrations/public-iac", json={"enabled": True, "settings": {
        "state_passphrase": "correct-horse-battery", **CF, "cloudflare_api_token": "cf-token"}})
    assert r.status_code == 200, r.get_json()
    return admin


def test_plan_and_apply_through_labdex(public_site, monkeypatch, tmp_path):
    seen = {}

    def fake_preview(self, resources, log=print):
        seen["preview"] = [r.name for r in resources]
        seen["env"], seen["stack"], seen["dir"] = dict(self.env), self.stack_name, str(self.state_dir)
        return [engine.Step("create", "cloudflare:index/dnsRecord:DnsRecord", r.name, new={"inputs": r.props}) for r in resources]

    def fake_up(self, resources, log=print):
        seen["up"] = [r.name for r in resources]
        return {"summary": {"create": len(resources)}, "outputs": {}}

    monkeypatch.setattr(engine.Engine, "preview", fake_preview)
    monkeypatch.setattr(engine.Engine, "up", fake_up)
    c = public_site
    r = c.post("/api/integrations/public-iac/actions/up")
    assert r.status_code in (200, 201, 202), r.get_json()
    job = c.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert job["status"] == "planned", job
    # only the public site's host, by its name (no fun name)
    assert seen["preview"] == ["srv-vm-oak-01.node.example.com/A"]
    assert seen["stack"] == "public" and seen["env"] == {"CLOUDFLARE_API_TOKEN": "cf-token"} and seen["dir"].endswith("pulumi/public-iac")
    assert [ch["name"] for ch in job["plan"]] == ["srv-vm-oak-01.node.example.com/A"]
    r = c.post(f"/api/jobs/{job['id']}/apply")
    assert r.status_code in (200, 202), r.get_json()
    assert c.get(f"/api/jobs/{job['id']}").get_json()["status"] == "applied"
    assert seen["up"] == seen["preview"]

    # resources: a dry run without Pulumi
    r = c.post("/api/integrations/public-iac/actions/resources")
    job = c.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert job["status"] == "succeeded" and job["result"]["resources"][0]["props"]["content"] == "203.0.113.11"
    integ = db.session.scalar(db.select(Integration).where(Integration.name == "public-iac"))
    assert integ.site == "public"


def test_plan_fails_cleanly_without_pulumi(public_site, monkeypatch):
    monkeypatch.setattr(engine, "available", lambda: (False, "the pulumi CLI isn't on the PATH"))
    r = public_site.post("/api/integrations/public-iac/actions/up")
    job = public_site.get(f"/api/jobs/{r.get_json()['id']}").get_json()
    assert job["status"] == "failed" and "pulumi CLI" in (job.get("log") or "") + str(job.get("result"))


@pytest.mark.skipif(not (os.environ.get("LABDEX_PULUMI_E2E") and shutil.which("pulumi")), reason="opt-in: LABDEX_PULUMI_E2E=1 with the pulumi CLI")
def test_real_preview_of_an_empty_program(tmp_path):
    """With the CLI installed: a stack in a folder, an empty program, nothing to change."""
    eng = engine.Engine(project="labdex-test", stack="test", state_dir=tmp_path, passphrase="test-passphrase")
    assert eng.preview([]) == []


def test_backups_carry_the_stacks(app, tmp_path, monkeypatch):
    import io
    import tarfile

    from labdex import backup
    monkeypatch.setitem(app.config, "LABDEX_DATA_DIR", str(tmp_path))
    assert backup.pulumi_state() == b""                                                    # no Pulumi integration: nothing
    root = tmp_path / "pulumi" / "public-iac"
    (root / ".pulumi" / "stacks" / "labdex-public-iac").mkdir(parents=True)
    (root / ".pulumi" / "stacks" / "labdex-public-iac" / "public.json").write_text("{}")
    (root / "home" / "plugins").mkdir(parents=True)
    (root / "home" / "plugins" / "big-binary").write_bytes(b"x" * 100)
    (root / "work").mkdir()
    (root / "work" / "Pulumi.yaml").write_text("name: x")
    _, blob, manifest = backup.create("test")
    files = backup.open_archive(blob)
    with tarfile.open(fileobj=io.BytesIO(files["pulumi-state.tar.gz"])) as tar:
        assert tar.getnames() == ["pulumi/public-iac/.pulumi/stacks/labdex-public-iac/public.json"]


def test_host_panel(public_site):
    r = public_site.get("/api/hosts/srv-vm-oak-01/modules/public-iac/view")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["sections"][0]["rows"][0]["name"] == "srv-vm-oak-01.node.example.com/A", d
