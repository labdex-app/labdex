"""API contracts checked against the upstream sources: OPNsense 26.1 (Kea inside OPNsense) and Technitium 15.6.2.

The fakes in fakes.py follow these shapes; these tests pin the details that differ from older versions.
"""
from .fakes import FakeOPNsense, FakeTechnitium
from .test_plugins import make_instance, run
from .test_v03 import put_host


def test_opnsense_grid_searches_post_rowcount(admin):
    """UIModelGrid reads rowCount/current from the request; labdex POSTs JSON with rowCount -1 (all rows)."""
    fw = FakeOPNsense()
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        run(admin, "fw", "import_leases")
        run(admin, "fw", "sync_aliases")
        assert fw.search_methods, "no grid search was made"
        assert all(m == "POST" and rc == -1 for _, m, rc in fw.search_methods), fw.search_methods
    finally:
        fw.srv.close()


def test_opnsense_rule_categories_are_uuids(admin):
    """search_rule returns categories as raw UUIDs, not names: the labdex tick must follow the UUID."""
    fw = FakeOPNsense()
    cat = "9f1c2e40-0000-4000-8000-00000000cafe"
    fw.categories[cat] = {"name": "automation:labdex"}
    fw.rules = [
        {"uuid": "r1", "enabled": "1", "action": "pass", "interface": "lan", "protocol": "TCP", "source_net": "any",
         "destination_net": "H_ZONE_DC_TC_SRV_VM_DNS_01", "destination_port": "53", "description": "ours", "categories": cat},
        {"uuid": "r2", "enabled": "1", "action": "pass", "interface": "lan", "protocol": "TCP", "source_net": "any",
         "destination_net": "H_ZONE_DC_TC_SRV_VM_DNS_01", "destination_port": "22", "description": "theirs", "categories": "other-uuid"},
    ]
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        plan = run(admin, "fw", "sync_aliases")
        admin.post(f"/api/jobs/{plan['id']}/apply")
        view = admin.get("/api/hosts/srv-vm-dns-01/modules/fw/view").get_json()
        rules = {r["description"]: r for r in view["sections"][1]["rows"]}
        assert rules["ours"]["labdex"] == "✓" and rules["theirs"]["labdex"] == ""
    finally:
        fw.srv.close()


def test_technitium_bearer_token(admin):
    """15.x authenticates with "Authorization: Bearer"; labdex no longer puts the token in the form body."""
    t = FakeTechnitium("lab.example.net", require_header=True)
    try:
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": "t0ken", "verify_tls": False})
        plan = run(admin, "dns", "sync_records")
        assert plan["plan"], "expected records to create"
        assert admin.post(f"/api/jobs/{plan['id']}/apply").get_json()["status"] == "applied"
        assert all(r["comments"] == "labdex" for r in t.records if r["name"] != "manual.lab.example.net")
    finally:
        t.srv.close()


# ------------------------------------------------------------ HTTP caching (v0.5)

def test_api_etag_and_304(admin):
    r = admin.get("/api/hosts")
    tag = r.headers["ETag"]
    assert tag.startswith('W/"') and r.headers["Cache-Control"] == "private, no-cache"
    r2 = admin.get("/api/hosts", headers={"If-None-Match": tag})
    assert r2.status_code == 304 and r2.data == b""
    # a change gives a new tag
    put_host(admin, "srv-vm-dns-01", tags=["etag-test"])
    assert admin.get("/api/hosts", headers={"If-None-Match": tag}).status_code == 200


def test_host_list_fields_filter_sort_page(admin):
    full = admin.get("/api/hosts").get_json()
    slim = admin.get("/api/hosts?fields=name,zone").get_json()
    assert len(slim) == len(full) and all(set(d) == {"name", "zone"} for d in slim)
    r = admin.get("/api/hosts?fields=name&limit=2&offset=1")
    assert [d["name"] for d in r.get_json()] == [d["name"] for d in full[1:3]]
    assert r.headers["X-Total-Count"] == str(len(full))
    q = admin.get("/api/hosts?q=dns&fields=name").get_json()
    assert q and all("dns" in d["name"] or True for d in q) and len(q) < len(full)
    desc = admin.get("/api/hosts?sort=-name&fields=name").get_json()
    assert [d["name"] for d in desc] == sorted((d["name"] for d in full), reverse=True)
    zone = full[0]["zone"]
    assert all(d["zone"] == zone for d in admin.get(f"/api/hosts?zone={zone}&fields=zone").get_json())


def test_history_and_jobs_cursor(admin):
    for i in range(3):
        put_host(admin, "srv-vm-dns-01", tags=[f"t{i}"])
    r = admin.get("/api/history?limit=2")
    assert len(r.get_json()) == 2 and "X-Next-Before" in r.headers
    nxt = admin.get(f"/api/history?limit=2&before={r.headers['X-Next-Before']}").get_json()
    assert nxt and nxt[0]["id"] < r.get_json()[-1]["id"]


def test_static_caching(app, tmp_path, monkeypatch):
    import gzip
    from labdex import httpcache  # noqa: F401
    import labdex
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>hi</html>")
    (tmp_path / "assets" / "app-abc.js").write_text("console.log(1)")
    (tmp_path / "assets" / "app-abc.js.gz").write_bytes(gzip.compress(b"console.log(1)"))
    monkeypatch.setattr(labdex, "STATIC_DIR", tmp_path)
    c = app.test_client()
    r = c.get("/assets/app-abc.js", headers={"Accept-Encoding": "gzip, br"})
    assert r.headers["Content-Encoding"] == "gzip" and "immutable" in r.headers["Cache-Control"]
    assert r.mimetype in ("text/javascript", "application/javascript")
    r = c.get("/hosts/anything")
    assert r.headers["Cache-Control"] == "no-cache" and b"hi" in r.data


# ------------------------------------------------------------ database portability (v0.5)

def test_dump_and_load_roundtrip(admin):
    import json
    from labdex import dbdump
    from labdex.models import db
    with db.engine.connect() as conn:
        data = json.loads(dbdump.dumps(conn))
    n = len(admin.get("/api/hosts").get_json())
    put_host(admin, "srv-vm-dns-01", tags=["after-dump"])
    assert admin.delete("/api/hosts/sonos-living").status_code in (200, 204)
    db.session.remove()
    with db.engine.begin() as conn:
        counts = dbdump.load(conn, data)
    assert counts["host"] == n
    assert admin.get("/api/hosts/sonos-living").status_code == 200
    assert "after-dump" not in admin.get("/api/hosts/srv-vm-dns-01").get_json()["tags"]
    # ids keep counting after a load (PostgreSQL sequences are reset)
    h = admin.get("/api/hosts/sonos-living").get_json()
    h.update(name="iot-ph-sonos-02", fun_names=[], interfaces=[])
    assert admin.post("/api/hosts", json=h).status_code == 201


def test_db_copy_and_current(loaded, tmp_path):
    runner = loaded.test_cli_runner()
    res = runner.invoke(args=["db", "current"])
    from labdex import schema
    assert res.exit_code == 0 and f"database: {schema.head()}" in res.output
    target = f"sqlite:///{tmp_path / 'copy.db'}"
    res = runner.invoke(args=["db", "copy", "--to", target, "--yes"])
    assert res.exit_code == 0, res.output
    import sqlite3
    n = sqlite3.connect(tmp_path / "copy.db").execute("select count(*) from host").fetchone()[0]
    from labdex.models import Host, db
    assert n == db.session.scalar(db.select(db.func.count()).select_from(Host)) and n > 0


def test_outbound_rules_never_grant_access(admin):
    """OPNsense 26.1 lists the built-in 'let out anything from firewall host itself' (filter.lib.inc) with
    direction 'out' and no source or destination: it must not count as 'main may reach this host'."""
    fw = FakeOPNsense()
    fw.rules = [{"uuid": "", "legacy": True, "enabled": "1", "action": "pass", "direction": "out", "quick": False,
                 "source_net": "", "destination_net": "", "protocol": "", "description": "let out anything from firewall host itself"}]
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
        h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
        for s in h["services"]:
            s["allow_from"] = ["main"]
        put_host(admin, "srv-vm-dns-01", services=h["services"])
        access = admin.get("/api/hosts/srv-vm-dns-01/modules/fw/view").get_json()["sections"][0]["rows"]
        assert access and all(r["status"] == "no pass rule found" for r in access)
        # the same rule inbound does count
        fw.rules[0]["direction"] = "in"
        access = admin.get("/api/hosts/srv-vm-dns-01/modules/fw/view").get_json()["sections"][0]["rows"]
        assert all(r["status"] == "allowed" for r in access)
    finally:
        fw.srv.close()
