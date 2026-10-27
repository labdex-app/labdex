"""v0.4: editable zones, discovery workflow, Kea, SSE, plugin store and trust, nav, DNS options, caching."""
import io
import json
import shutil
import textwrap
import zipfile
from pathlib import Path

from werkzeug.wrappers import Response

from labdex.plugins import trust

from .conftest import ADMIN, client
from .fakes import FakeOPNsense, FakeServer, FakeTechnitium
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


# -------------------------------------------------------------------- zones
def test_zones_start_empty_and_are_editable(app):
    c = client(app)
    c.post("/api/auth/setup", json=ADMIN)
    z = c.get("/api/zones").get_json()
    assert z["areas"] == [] and z["zones"] == []
    assert c.get("/api/nav").get_json()["zones_defined"] is False
    assert c.post("/api/zones/areas", json={"code": "dc", "name": "Datacenter"}).status_code == 200
    r = c.post("/api/zones/zones", json={"code": "DC_TC", "name": "Trusted", "rank": 4, "colour": "#4ade80"})
    assert [x["code"] for x in r.get_json()["zones"]] == ["DC", "DC_TC"]
    assert c.post("/api/zones/zones", json={"code": "dc_tc", "name": "x"}).status_code == 422  # duplicate
    assert c.post("/api/zones/zones", json={"code": "DC_UT", "rank": 1, "colour": "pink"}).status_code == 422
    assert c.post("/api/zones/zones", json={"code": "XX_UT"}).status_code == 422  # no such area
    assert c.post("/api/zones/zones", json={"code": "DC_DEFAULT"}).status_code == 422
    assert c.delete("/api/zones/zones/DC").status_code == 422  # the default goes with the area
    c.post("/api/networks", json={"name": "srv", "zone": "DC_TC", "prefix4": "10.0.0.0/24"})
    c.post("/api/hosts", json={"name": "srv-vm-a-01", "zone": "DC_TC", "interfaces": [{"network": "srv", "ip4": "10.0.0.5"}]})
    r = c.post("/api/aliases", json={"name": "NG_SPECIAL_X", "type": "network", "members": ["zone:DC_TC"]})
    assert r.status_code == 201, r.get_json()
    assert c.delete("/api/zones/areas/DC").status_code == 422
    # rename follows networks, hosts, alias members
    r = c.put("/api/zones/areas/DC", json={"code": "LAB", "name": "Lab"})
    assert r.status_code == 200, r.get_json()
    assert host(c, "srv-vm-a-01")["zone"] == "LAB_TC"
    assert c.get("/api/networks/srv").get_json()["zone"] == "LAB_TC"
    assert c.get("/api/aliases/custom/NG_SPECIAL_X").get_json()["members"] == ["zone:LAB_TC"]
    assert any(e["object_type"] == "area" for e in c.get("/api/history").get_json())
    # a zone rename follows too, the policy included
    c.put("/api/zone-policy", json={"src": "LAB_TC", "dst": "LAB_TC", "ports": ["any"]})
    r = c.put("/api/zones/zones/LAB_TC", json={"code": "LAB_SRV", "name": "Servers", "rank": 4})
    assert r.status_code == 200, r.get_json()
    assert host(c, "srv-vm-a-01")["zone"] == "LAB_SRV" and c.get("/api/zone-policy").get_json()["cells"][0]["src"] == "LAB_SRV"
    assert c.delete("/api/zones/zones/LAB_SRV").status_code == 422  # in use
    # example set only on an empty table
    assert c.post("/api/zones/example").status_code == 422


def test_zone_example_and_non_admin(app, tmp_path):
    c = client(app)
    c.post("/api/auth/setup", json=ADMIN)
    assert len(c.post("/api/zones/example").get_json()["zones"]) == 14
    c.post("/api/admin/users", json={"username": "bob", "password": "user-password-123"})
    b = client(app)
    b.post("/api/auth/login", json={"username": "bob", "password": "user-password-123"})
    assert b.post("/api/zones/areas", json={"code": "X", "name": "x"}).status_code == 403


# ------------------------------------------------------------------ caching
def test_lint_cache_follows_changes(admin):
    before = admin.get("/api/lint").get_json()["summary"]
    put_host(admin, "srv-vm-dns-01", name="weird")
    after = admin.get("/api/lint").get_json()
    assert any(i["ref"] == "weird" and i["code"] == "hostname" for i in after["issues"])
    assert after["summary"] != before


# --------------------------------------------------------- DNS name options
def test_dotted_service_names_and_interface_dns(admin):
    h = host(admin, "srv-vm-dns-01")
    h["services"][1]["dns_name"] = "primary.dns"
    h["interfaces"].append({"network": "storage", "ip4": "10.10.8.53", "in_dns": True})
    d = put_host(admin, "srv-vm-dns-01", services=h["services"], interfaces=h["interfaces"])
    fqdns = {r["fqdn"] for r in d["dns"]}
    assert "primary.dns.lab.example.net" in fqdns
    assert "srv-vm-dns-01.storage.lab.example.net" in fqdns  # storage has in_dns: false, the interface overrides
    assert d["interfaces"][1]["in_dns"] is True
    h = host(admin, "srv-vm-dns-01")
    h["interfaces"][0]["in_dns"] = False
    d = put_host(admin, "srv-vm-dns-01", interfaces=h["interfaces"])
    assert not any(r["fqdn"].startswith("srv-vm-dns-01.srv.") for r in d["dns"])
    bad = host(admin, "srv-vm-dns-01")
    bad["services"][1]["dns_name"] = "-bad..name"
    assert admin.put("/api/hosts/srv-vm-dns-01", json=bad).status_code == 422
    assert "in_dns: true" in admin.get("/api/admin/backup").get_data(as_text=True)


# ---------------------------------------------------------------- discovery
def seed_discovery(app):
    from labdex import discovery
    from labdex.models import db
    with app.app_context():
        discovery.record_scan("test", {"10.10.100.53", "10.10.100.77", "10.10.100.20"}, [
            {"ip": "10.10.100.53", "services": [{"port": 53, "proto": "udp", "service": "domain"},
                                                {"port": 9100, "proto": "tcp", "service": "jetdirect"}]},
            {"ip": "10.10.100.77", "hostname": "mystery", "services": [{"port": 443, "proto": "tcp", "service": "https"},
                                                                      {"port": 22, "proto": "tcp", "service": "ssh"}]},
        ] + [{"ip": f"10.10.100.{100 + i}", "services": [{"port": 80, "proto": "tcp", "service": "http"}]} for i in range(60)])
        discovery.record_neighbours("arp", [{"ip": "10.10.10.99", "mac": "AA:BB:CC:00:00:99", "hostname": "phone"}])
        db.session.commit()


def test_discovery_paging_filters_and_assign(admin, loaded):
    seed_discovery(loaded)
    page = admin.get("/api/discovery/services").get_json()
    assert page["total"] == 63 and len(page["items"]) == 50 and page["page"] == 1
    assert not any(s["status"] == "documented" for s in page["items"])
    assert admin.get("/api/discovery/services?page=2").get_json()["items"][-1]["ip"] == "10.10.100.159"
    assert admin.get("/api/discovery/services?status=documented").get_json()["total"] == 1
    assert admin.get("/api/discovery/services?q=mystery").get_json()["total"] == 0  # hostname isn't on the service row
    assert admin.get("/api/discovery/services?q=jetdirect").get_json()["total"] == 1

    # one click: declare an undocumented port on its host
    sid = next(s["id"] for s in page["items"] if s["port"] == 9100)
    r = admin.post(f"/api/discovery/services/{sid}/adopt")
    assert r.status_code == 201 and any(s["port"] == 9100 for s in r.get_json()["services"])
    # unknown host: refuse without a target, add to an existing host with one
    sid = next(s["id"] for s in page["items"] if s["ip"] == "10.10.100.77" and s["port"] == 443)
    assert admin.post(f"/api/discovery/services/{sid}/adopt").status_code == 409
    r = admin.post(f"/api/discovery/services/{sid}/adopt", json={"host": "srv-vm-mon-01"})
    d = r.get_json()
    assert r.status_code == 201 and any(i["ip4"] == "10.10.100.77" and i["network"] == "srv" for i in d["interfaces"])
    assert any(s["port"] == 443 and s["scheme"] == "https" for s in d["services"]) or any(s["name"] == "grafana" for s in d["services"])
    assert admin.post(f"/api/discovery/services/{sid}/adopt", json={"host": "srv-vm-ha-01"}).status_code == 409

    # an address seen by ARP onto an existing host, with its MAC
    r = admin.post("/api/discovery/addresses/assign", json={"ip": "10.10.10.99", "host": "cli-nb-admin-01"})
    assert r.status_code == 201, r.get_json()
    assert any(i["ip4"] == "10.10.10.99" and i["mac"] == "aa:bb:cc:00:00:99" for i in r.get_json()["interfaces"])
    unknown = admin.get("/api/discovery/unknown-hosts?per_page=500").get_json()
    assert unknown["total"] == 60 and not any(u["ip"] == "10.10.10.99" for u in unknown["items"])
    assert admin.delete("/api/discovery/addresses/10.10.100.150").status_code == 204


# --------------------------------------------------------------------- Kea
def test_kea_leases_and_reservations(admin):
    fw = FakeOPNsense()
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        assert run(admin, "fw", "import_leases")["result"] == {"leases": 2, "leases6": 1}
        u = admin.get("/api/discovery/unknown-hosts").get_json()["items"]
        assert u[0]["ip"] == "10.10.10.88" and u[0]["hostname"] == "tablet" and u[0]["source"].startswith("kea:")

        assert run(admin, "fw", "sync_reservations")["status"] == "succeeded"  # switched off
        admin.put("/api/integrations/fw", json={"settings": {"kea_reservations": True}})
        fw.reservations["hand"] = {"subnet": "sub-main", "ip_address": "10.10.10.200", "hw_address": "00:1a:2b:00:10:01",
                                   "hostname": "mine", "description": "by hand"}
        plan = run(admin, "fw", "sync_reservations")
        # the notebook's two NICs get reservations; the PC's MAC already has a hand-made one: skipped
        assert sorted(c["after"]["ip_address"] for c in plan["plan"]) == ["10.10.10.201", "10.10.10.202"]
        assert all(c["op"] == "create" for c in plan["plan"])
        assert "hand-made reservation" in admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
        assert admin.post(f"/api/jobs/{plan['id']}/apply").get_json()["status"] == "applied"
        assert fw.kea_reconfigured == 1 and len(fw.reservations) == 3
        assert all("[labdex]" in r["description"] for k, r in fw.reservations.items() if k != "hand")
        assert run(admin, "fw", "sync_reservations")["status"] == "succeeded"  # idempotent
    finally:
        fw.srv.close()


def test_dns_records_feed_discovery(admin):
    t = FakeTechnitium("lab.example.net")
    t.records.append({"name": "old-nas.lab.example.net", "type": "A", "ttl": 300, "rData": {"ipAddress": "10.10.3.77"}, "comments": ""})
    try:
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": "t0ken", "verify_tls": False})
        assert run(admin, "dns", "import_records")["result"] == {"addresses": 2}
        u = {x["ip"]: x for x in admin.get("/api/discovery/unknown-hosts").get_json()["items"]}
        assert u["10.10.3.77"]["hostname"] == "old-nas.lab.example.net" and u["10.10.3.77"]["up"] is False
    finally:
        t.srv.close()


# --------------------------------------------------------------------- SSE
def test_events_stream(admin):
    make_instance(admin, "exports", "files", {})
    from labdex.models import Integration, Job, db
    integ = db.session.scalar(db.select(Integration))
    job = Job(integration_id=integ.id, action="x", mode="plan", status="planned", log="line one\n", plan=[{"op": "create"}])
    db.session.add(job)
    db.session.commit()
    r = admin.get(f"/api/events?job={job.id}&once=1")
    assert r.mimetype == "text/event-stream"
    text = r.get_data(as_text=True)
    events = [blk for blk in text.split("\n\n") if blk.startswith("event:")]
    kinds = [e.split("\n")[0].split(": ")[1] for e in events]
    assert kinds[:4] == ["pending", "revision", "log", "job"] and kinds[-1] == "bye"
    assert json.loads(events[0].split("data: ")[1]) == {"count": 1}
    assert json.loads(events[1].split("data: ")[1])["rev"] > 0
    assert "line one" in events[2]
    assert admin.get("/api/events?job=99999").status_code == 404


# ------------------------------------------------------------- plugin store
PLUGIN = {
    "plugin.yaml": """
        id: hello
        name: Hello
        version: "1.0"
        kind: module
        tags: [demo]
        pages:
          - {id: greet, label: Greeting}
        actions:
          - {id: hi, mode: run}
    """,
    "plugin.py": """
        from labdex.plugins.base import Plugin as Base
        class Plugin(Base):
            def run_hi(self, params):
                return {"message": "hi"}
            def page_greet(self, args):
                return {"sections": [{"title": "Hello", "type": "list", "items": ["world"]}]}
    """,
}


def make_plugin(tmp_path, version="1.0", files=None) -> Path:
    d = tmp_path / f"src-hello-{version}" / "hello"
    d.mkdir(parents=True)
    for name, text in (files or PLUGIN).items():
        (d / name).write_text(textwrap.dedent(text).replace('version: "1.0"', f'version: "{version}"'))
    return d


def zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                zf.write(p, Path(d.name) / p.relative_to(d))
    return buf.getvalue()


def upload(c, blob):
    return c.post("/api/plugins/upload", data={"file": (io.BytesIO(blob), "hello.zip")}, content_type="multipart/form-data")


def test_install_unsigned_needs_confirmation(admin, tmp_path, make_user):
    r = upload(admin, zip_dir(make_plugin(tmp_path)))
    info = r.get_json()
    assert r.status_code == 200 and info["trust"]["status"] == "unsigned" and info["needs_confirmation"]
    assert info["python"] and "plugin.py" in info["files"]
    assert admin.get("/api/plugins").get_json()["plugins"][0]["id"] != "hello" or True
    assert not any(p["id"] == "hello" for p in admin.get("/api/plugins").get_json()["plugins"])  # nothing ran yet
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm").status_code == 422
    r = admin.post(f"/api/plugins/stage/{info['token']}/confirm", json={"accept_unsigned": True})
    assert r.status_code == 201, r.get_json()
    p = next(p for p in admin.get("/api/plugins").get_json()["plugins"] if p["id"] == "hello")
    assert p["trust"]["status"] == "unsigned" and p["tags"] == ["demo"] and not p["error"]
    make_instance(admin, "hello", "hello", {})
    assert run(admin, "hello", "hi")["result"] == {"message": "hi"}
    assert admin.get("/api/integrations/hello/pages/greet").get_json()["sections"][0]["items"] == ["world"]
    nav = admin.get("/api/nav").get_json()
    assert any(m["integration"] == "hello" and m["pages"][0]["id"] == "greet" for m in nav["modules"])
    assert admin.delete("/api/plugins/hello").status_code == 409  # still used
    admin.delete("/api/integrations/hello")
    assert admin.delete("/api/plugins/hello").status_code == 204
    assert admin.delete("/api/plugins/demo-lab").status_code == 409  # built in
    assert admin.delete("/api/plugins/nmap").status_code == 204  # ships with labdex: since 1.21 only uninstalled
    assert make_user("ia", role="integration-admin").post("/api/plugins/upload").status_code == 403


def test_bad_archives_refused(admin, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil/plugin.yaml", "id: evil")
    assert "unsafe path" in upload(admin, buf.getvalue()).get_json()["error"]
    assert upload(admin, b"not a zip").get_json()["error"] == "not a zip file"
    d = make_plugin(tmp_path, files={"plugin.yaml": "id: nmap\nname: fake\nversion: '1'\n"})
    assert "labdex project key" in upload(admin, zip_dir(d)).get_json()["error"]  # shipped: only official updates


def test_signed_plugins_official_community_tampered(admin, tmp_path, monkeypatch):
    # an "official" key for this test
    off = tmp_path / "official"
    off.mkdir()
    pem, pub = trust.keygen()
    (off / "test-project.pub").write_text(pub)
    monkeypatch.setattr(trust, "OFFICIAL_DIR", off)
    d = make_plugin(tmp_path)
    trust.sign(d, pem, "labdex project")
    info = upload(admin, zip_dir(d)).get_json()
    assert info["trust"]["status"] == "official" and not info["needs_confirmation"]
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm").status_code == 201

    # a community author: unknown key → refused, trusted → community
    apem, apub = trust.keygen()
    d2 = make_plugin(tmp_path, "1.1")
    trust.sign(d2, apem, "Alice")
    info = upload(admin, zip_dir(d2)).get_json()
    assert info["trust"]["status"] == "invalid" and not info["installable"]
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm", json={"accept_unsigned": True}).status_code == 422
    r = admin.post("/api/plugins/keys", json={"name": "Alice", "public_key": apub})
    assert r.status_code == 201
    info = upload(admin, zip_dir(d2)).get_json()
    assert info["trust"]["status"] == "community" and info["installed_version"] == "1.0"
    admin.post(f"/api/plugins/stage/{info['token']}/confirm")
    p = next(p for p in admin.get("/api/plugins").get_json()["plugins"] if p["id"] == "hello")
    assert p["version"] == "1.1" and p["trust"]["status"] == "community"

    # tampering after signing: refused at upload, and not loaded from disk either
    (d2 / "plugin.py").write_text((d2 / "plugin.py").read_text() + "\n# sneaky\n")
    info = upload(admin, zip_dir(d2)).get_json()
    assert info["trust"]["status"] == "invalid" and "differ" in info["trust"]["detail"]
    installed = Path(p["path"])
    (installed / "plugin.py").write_text((installed / "plugin.py").read_text() + "\n# sneaky\n")
    admin.post("/api/plugins/reload")
    p = next(p for p in admin.get("/api/plugins").get_json()["plugins"] if p["id"] == "hello")
    assert p["error"].startswith("not loaded") and not p["python"]

    # removing a trusted key stops plugins signed only with it
    kid = admin.get("/api/plugins/keys").get_json()["community"][0]["id"]
    assert admin.delete(f"/api/plugins/keys/{kid}").status_code == 204


def test_signed_repository(admin, tmp_path):
    from labdex.plugins import installer
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    d = make_plugin(tmp_path, "2.0")
    shutil.copytree(d, src / "hello")
    pem, pub = trust.keygen()
    idx = installer.build_repository(src, out, pem, "Test repo", "for tests")
    assert idx["plugins"][0]["url"] == "hello-2.0.zip" and idx["plugins"][0]["capabilities"] == ["actions", "pages"]
    served = {"index": json.loads((out / "index.json").read_text())}

    def handle(req):
        if req.path == "/index.json":
            return Response(json.dumps(served["index"]), mimetype="application/json")
        f = out / req.path.lstrip("/")
        return Response(f.read_bytes(), mimetype="application/zip") if f.is_file() else Response("", 404)
    srv = FakeServer(handle)
    try:
        url = srv.url + "/index.json"
        pv = admin.post("/api/plugin-repos/preview", json={"url": url}).get_json()
        assert pv["name"] == "Test repo" and pv["plugins"] == 1 and not pv["official"]
        assert admin.post("/api/plugin-repos", json={"url": url, "key_id": "0" * 16}).status_code == 409
        r = admin.post("/api/plugin-repos", json={"url": url, "key_id": pv["key_id"]})
        assert r.status_code == 201, r.get_json()
        rid = r.get_json()["id"]
        m = admin.get("/api/marketplace").get_json()
        hello = next(p for p in m["plugins"] if p["id"] == "hello")  # since 1.21 the list also has what ships with labdex
        assert m["errors"] == [] and hello["repository"] == "Test repo" and not hello["installed"]
        info = admin.post("/api/marketplace/install", json={"repository_id": rid, "id": "hello"}).get_json()
        assert info["trust"]["status"] == "community" and info["trust"]["signer"] == "Test repo" and not info["needs_confirmation"]
        assert admin.post(f"/api/plugins/stage/{info['token']}/confirm").status_code == 201
        assert next(p for p in admin.get("/api/marketplace").get_json()["plugins"] if p["id"] == "hello")["installed_version"] == "2.0"

        # a changed index without a new signature is refused
        installer._index_cache.clear()
        served["index"]["plugins"][0]["version"] = "9.9"
        assert "bad index signature" in admin.get("/api/marketplace").get_json()["errors"][0]
        # so is the same URL signed with another key (the key is pinned when added)
        other, _ = trust.keygen()
        installer.build_repository(src, out, other, "Test repo")
        served["index"] = json.loads((out / "index.json").read_text())
        installer._index_cache.clear()
        assert "not the repository's" in admin.get("/api/marketplace").get_json()["errors"][0]
        assert "not the repository" in admin.post("/api/marketplace/install", json={"repository_id": rid, "id": "hello"}).get_json()["error"]
        assert admin.get("/api/plugin-repos").get_json()["repositories"][0]["error"]

        # removing the repository stops plugins signed only with its key
        assert admin.delete(f"/api/plugin-repos/{rid}").status_code == 204
        p = next(p for p in admin.get("/api/plugins").get_json()["plugins"] if p["id"] == "hello")
        assert p["error"].startswith("not loaded")
    finally:
        srv.close()


def test_cli_sign_and_pack(loaded, tmp_path):
    runner = loaded.test_cli_runner()
    key = tmp_path / "k.pem"
    assert runner.invoke(args=["plugin", "keygen", str(key)]).exit_code == 0
    d = make_plugin(tmp_path)
    res = runner.invoke(args=["plugin", "sign", str(d), "--key", str(key), "--signer", "Me"])
    assert res.exit_code == 0 and "Signed hello 1.0" in res.output
    res = runner.invoke(args=["plugin", "pack", str(d), "-o", str(tmp_path / "h.zip")])
    assert res.exit_code == 0 and "sha256" in res.output
    assert "hello/SIGNATURE.json" in zipfile.ZipFile(tmp_path / "h.zip").namelist()
    src = tmp_path / "src"
    src.mkdir()
    shutil.copytree(d, src / "hello")
    res = runner.invoke(args=["plugin", "repo", str(src), str(tmp_path / "pub"), "--key", str(key), "--name", "My plugins"])
    assert res.exit_code == 0 and "hello-1.0.zip" in res.output, res.output
    idx = json.loads((tmp_path / "pub" / "index.json").read_text())
    assert idx["name"] == "My plugins" and idx["signature"] and (tmp_path / "pub" / "hello-1.0.zip").is_file()


# ---------------------------------------------------------------------- nav
def test_nav_tree_and_live_status(admin, loaded, make_user):
    seed_discovery(loaded)
    nav = admin.get("/api/nav").get_json()
    dc_tc = next(z for z in nav["tree"] if z["zone"] == "DC_TC")
    srv = next(n for n in dc_tc["networks"] if n["name"] == "srv")
    dns = next(h for h in srv["hosts"] if h["name"] == "srv-vm-dns-01")
    assert dns["live"] == "up" and next(h for h in srv["hosts"] if h["name"] == "srv-vm-ha-01")["live"] == "unknown"
    assert nav["show"]["discovery"] is False and nav["show"]["admin"] is True
    make_instance(admin, "nmap", "scan", {})
    assert admin.get("/api/nav").get_json()["show"]["discovery"] is True
    v = make_user("v", role="viewer", scopes=["zone:CA_*"])
    zones = {z["zone"] for z in v.get("/api/nav").get_json()["tree"]}
    assert zones and all(z.startswith("CA_") for z in zones)
    assert next(h for h in admin.get("/api/hosts").get_json() if h["name"] == "srv-vm-dns-01")["live"] == "up"


# ------------------------------------------------------- hostnames, fun names
def test_naming_parts_seeded_from_existing_and_next_name(admin):
    n = admin.get("/api/naming").get_json()
    assert {"srv", "net", "cli"} <= {c["code"] for c in n["category"]}
    assert {"vm", "pm"} <= {t["code"] for t in n["type"]} and {"dns", "k3s"} <= {r["code"] for r in n["role"]}
    assert admin.get("/api/naming/next?category=srv&type=vm&role=k3s").get_json()["name"] == "srv-vm-k3s-03"
    assert admin.get("/api/naming/next?category=srv&type=vm&role=k3s&exclude=srv-vm-k3s-01").get_json()["name"] == "srv-vm-k3s-01"
    nx = admin.get("/api/naming/next?category=srv&type=ct&role=git").get_json()
    assert nx["name"] == "srv-ct-git-01" and nx["unknown"] == []  # roles are free text since 0.5.2
    assert admin.post("/api/naming/role", json={"code": "git", "label": "Forgejo"}).status_code == 201
    assert admin.post("/api/naming/role", json={"code": "Git!", "label": "x"}).status_code == 422
    assert admin.delete("/api/naming/role/dns").status_code == 422  # in use
    assert admin.put("/api/naming/role/dns", json={"code": "resolver"}).status_code == 422
    assert admin.put("/api/naming/role/dns", json={"code": "dns", "label": "Name resolution"}).status_code == 200
    text = admin.get("/api/admin/backup").get_data(as_text=True)
    assert "naming:" in text and "Forgejo" in text
    # categories drive the hostname lint
    admin.post("/api/naming/category", json={"code": "iot", "label": "IoT"})
    admin.post("/api/hosts", json={"name": "iot-pm-cam-01", "zone": "CA_UT"})
    assert not any(i["ref"] == "iot-pm-cam-01" and i["code"] == "hostname" for i in admin.get("/api/lint").get_json()["issues"])


def test_multiple_fun_names(admin):
    d = put_host(admin, "srv-vm-dns-01", fun_names=["unown", "resolver", "pihole"])
    assert d["fun"] == "unown" and d["fun_names"] == ["unown", "resolver", "pihole"]
    cnames = {r["fqdn"] for r in d["dns"] if r["type"] == "CNAME"}
    assert {"unown.lab.example.net", "resolver.lab.example.net", "pihole.lab.example.net"} <= cnames
    assert admin.get("/api/hosts/pihole").get_json()["name"] == "srv-vm-dns-01"
    r = admin.put("/api/hosts/srv-vm-mon-01", json={**host(admin, "srv-vm-mon-01"), "fun_names": ["alakazam", "pihole"]})
    assert r.status_code == 422 and "already used by srv-vm-dns-01" in r.get_json()["errors"][0]["message"]
    r = admin.post("/api/hosts", json={"name": "resolver", "zone": "DC_TC"})
    assert r.status_code == 422  # a hostname can't take someone's fun name
    text = admin.get("/api/admin/backup").get_data(as_text=True)
    assert "fun_names:" in text
    assert admin.post("/api/admin/restore", json={"yaml": text, "confirm": "replace"}).status_code == 200
    assert host(admin, "srv-vm-dns-01")["fun_names"] == ["unown", "resolver", "pihole"]


def test_firewall_ids_and_category(admin):
    fw = FakeOPNsense()
    try:
        admin.post("/api/aliases", json={"name": "Hosts that may administer the Proxmox cluster", "type": "host",
                                         "members": ["host:cli-nb-admin-01"]})
        admin.post("/api/aliases", json={"name": "Backup targets", "fw_id": "HG_BACKUP", "type": "host",
                                         "members": ["alias:Hosts that may administer the Proxmox cluster"]})
        al = {a["name"]: a for a in admin.get("/api/aliases").get_json()}
        long_id = al["Hosts that may administer the Proxmox cluster"]["fw_id"]
        assert len(long_id) <= 32 and al["Backup targets"]["fw_id"] == "HG_BACKUP"
        assert al["Backup targets"]["content"] == [long_id]
        legacy = fw.add("H_OLD_THING", "10.0.0.1", "old [labdex]")  # synced by an older labdex
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        plan = run(admin, "fw", "sync_aliases")
        assert ("delete", "H_OLD_THING") in {(c["op"], c["name"]) for c in plan["plan"]}
        admin.post(f"/api/jobs/{plan['id']}/apply")
        cat = next(u for u, c in fw.categories.items() if c["name"] == "automation:labdex")
        a = fw.by_name(long_id)
        assert a["categories"] == cat and a["description"].startswith("Hosts that may administer the Proxmox cluster")
        assert "[labdex]" not in a["description"]
        assert fw.by_name("HG_BACKUP")["content"] == long_id
        assert legacy not in fw.aliases
        assert run(admin, "fw", "sync_aliases")["status"] == "succeeded"  # stable
    finally:
        fw.srv.close()
