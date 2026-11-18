"""Patch state per host: reports from agents and plugins, merged into one state per host."""
from datetime import timedelta

from labdex import notify, patches
from labdex.models import HostPatch, Integration, db, utcnow

from .test_sites import add_public

DNS = "srv-vm-dns-01"
MON = "srv-vm-mon-01"


def test_agent_reports_one_host(admin):
    r = admin.post("/api/patches", json={"host": DNS, "os": "Ubuntu 24.04", "kernel": "6.8.0-45", "reboot": True,
                                         "packages": [{"name": "openssl", "from": "3.0.13-0ubuntu3.4", "to": "3.0.13-0ubuntu3.5", "security": True},
                                                      "curl"]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"saved": [DNS], "skipped": []}
    s = admin.get(f"/api/hosts/{DNS}/patches").get_json()
    assert s["state"] == "security" and s["pending"] == 2 and s["security"] == 1 and s["reboot"]
    assert s["os"] == "Ubuntu 24.04" and s["sources"][0]["source"] == "agent"
    assert admin.get(f"/api/hosts/{DNS}").get_json()["patches"]["state"] == "security"


def test_several_hosts_and_unknown_ones(admin):
    r = admin.post("/api/patches", json={"source": "ansible", "hosts": {DNS: {"pending": 0}, MON: {"pending": 4}, "nope-01": {}}})
    assert r.get_json()["saved"] == [DNS, MON] and r.get_json()["skipped"] == ["nope-01"]
    data = admin.get("/api/patches").get_json()
    by = {h["name"]: h for h in data["hosts"]}
    assert by[DNS]["state"] == "current" and by[MON]["state"] == "pending" and by[MON]["sources"] == ["ansible"]
    assert data["counts"]["current"] == 1 and data["counts"]["pending"] == 1 and data["counts"]["unknown"] > 0
    assert [h["name"] for h in admin.get("/api/patches?state=pending").get_json()["hosts"]] == [MON]
    assert admin.get("/api/patches?state=pending").get_json()["counts"] == data["counts"]  # counts ignore the filter
    assert admin.post("/api/patches", json={"hosts": {"nope-01": {}}}).status_code == 404
    assert admin.post("/api/patches", json={}).status_code == 422


def test_bad_values_save_nothing(admin):
    r = admin.post("/api/patches", json={"source": "x", "hosts": {DNS: {"pending": 1}, MON: {"pending": -1, "patched_at": "yesterday"}}})
    assert r.status_code == 422
    fields = {e["field"] for e in r.get_json()["errors"]}
    assert {f"{MON}.pending", f"{MON}.patched_at"} <= fields
    assert db.session.scalars(db.select(HostPatch)).first() is None
    assert admin.post("/api/patches", json={"source": "no/slash", "host": DNS}).status_code == 422


def test_merge_of_sources(app, loaded):
    patches.report("proxmox", {DNS: {"pending": 0, "patched_at": "2026-11-01T08:00:00+01:00"}})
    patches.report("agent", {DNS: {"pending": 2, "reboot": True, "patched_at": 1793836800}})
    s = patches.summary(DNS)
    assert s["state"] == "reboot" and s["pending"] == 2  # the latest report counts
    patches.report("proxmox", {DNS: {"pending": 0}})
    assert patches.summary(DNS)["state"] == "current"    # also for the reboot: a newer report clears it
    patches.report("agent", {DNS: {"pending": 2, "reboot": True, "patched_at": 1793836800}})
    assert s["patched_at"] == "2026-11-05T00:00:00Z"       # the most recent of the two, in UTC
    assert [x["source"] for x in s["sources"]] == ["agent", "proxmox"]
    # an old report is stale, and alone it makes the host stale
    db.session.execute(db.update(HostPatch).values(checked_at=utcnow() - timedelta(days=4)))
    db.session.commit()
    s = patches.summary(DNS)
    assert s["state"] == "stale" and not s["reboot"] and all(x["stale"] for x in s["sources"])
    patches.report("agent", {DNS: {"error": "apt is locked"}})
    assert patches.summary(DNS)["state"] == "error"
    assert patches.summary("srv-unknown-01")["state"] == "unknown"


def test_replace_drops_hosts_a_source_no_longer_lists(app, loaded):
    patches.report("proxmox", {DNS: {}, MON: {}})
    patches.report("proxmox", {MON: {}}, replace=True)
    assert patches.summary(DNS)["state"] == "unknown" and patches.summary(MON)["state"] == "current"


def test_notifies_only_when_it_becomes_urgent(app, loaded, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "emit", lambda event, title, *a, **kw: sent.append((event, title)))
    patches.report("agent", {DNS: {"pending": 1}})
    assert sent == []
    patches.report("agent", {DNS: {"packages": [{"name": "openssl", "security": True}], "reboot": True}})
    assert sent == [("host.patches", f"{DNS} needs 1 security updates and a reboot")]
    patches.report("agent", {DNS: {"packages": [{"name": "openssl", "security": True}], "reboot": True}})
    assert len(sent) == 1  # still the same news


def test_rename_and_delete_follow_the_host(admin):
    patches.report("agent", {MON: {"pending": 3}})
    h = admin.get(f"/api/hosts/{MON}").get_json()
    h["name"] = "srv-vm-mon-02"
    assert admin.put(f"/api/hosts/{MON}", json=h).status_code == 200
    assert patches.summary("srv-vm-mon-02")["pending"] == 3 and patches.summary(MON)["state"] == "unknown"
    assert admin.delete("/api/hosts/srv-vm-mon-02").status_code in (200, 204)
    assert db.session.scalars(db.select(HostPatch)).first() is None


def test_rights(admin, make_user):
    viewer = make_user("vic", role="viewer")
    assert viewer.post("/api/patches", json={"host": DNS}).status_code == 403
    assert viewer.get(f"/api/hosts/{DNS}/patches").status_code == 200
    token = admin.post("/api/auth/tokens", json={"name": "patch-report"}).get_json()["token"]
    bare = admin.application.test_client()
    r = bare.post("/api/patches", json={"host": DNS, "pending": 1}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_a_plugin_reports_for_its_site_only(admin):
    add_public(admin)
    r = admin.post("/api/integrations", json={"plugin_id": "exports", "name": "public-exports", "site": "public"})
    assert r.status_code == 201
    from labdex.plugins.base import Context
    reg = admin.application.extensions["labdex"]["registry"]
    integ = db.session.scalar(db.select(Integration).where(Integration.name == "public-exports"))
    ctx = Context(integ.name, reg.get("exports"), {}, lambda m: None, integration=integ)
    out = ctx.report_patches({"srv-vm-oak-01": {"pending": 5}, DNS: {"pending": 1}})
    assert out == {"saved": ["srv-vm-oak-01"], "skipped": [DNS]}
    assert patches.summary("srv-vm-oak-01")["sources"][0]["source"] == "public-exports"
    pub = admin.get("/api/patches?site=public").get_json()
    assert [h["name"] for h in pub["hosts"]] == ["srv-vm-oak-01"] and pub["counts"]["pending"] == 1


APT = """NOTE: This is only a simulation!
Inst openssl [3.0.13-0ubuntu3.4] (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst tzdata [2024a-3ubuntu1.1] (2025b-0ubuntu0.24.04 Ubuntu:24.04/noble-updates [all])
Inst linux-image-6.8.0-50-generic (6.8.0-50.51 Ubuntu:24.04/noble-updates [amd64])
Conf openssl (3.0.13-0ubuntu3.5 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
"""

DNF = """
Last metadata expiration check: 0:12:01 ago on Wed 18 Nov 2026 09:00:00 UTC.
openssl-libs.x86_64        1:3.0.7-28.el9_4        baseos
vim-minimal.x86_64         2:8.2.2637-21.el9       baseos
"""

DNF_SEC = "RHSA-2026:1234 Important/Sec. openssl-libs-1:3.0.7-28.el9_4.x86_64\n"


def test_agent_reads_apt_and_dnf():
    from labdex.agent import patch_report as pr
    pkgs = pr.parse_apt(APT)
    assert [(p["name"], p["from"], p["to"], p["security"]) for p in pkgs] == [
        ("openssl", "3.0.13-0ubuntu3.4", "3.0.13-0ubuntu3.5", True),
        ("tzdata", "2024a-3ubuntu1.1", "2025b-0ubuntu0.24.04", False),
        ("linux-image-6.8.0-50-generic", "", "6.8.0-50.51", False)]
    pkgs = pr.parse_dnf(DNF, DNF_SEC)
    assert [(p["name"], p["security"]) for p in pkgs] == [("openssl-libs", True), ("vim-minimal", False)]


def test_agent_finds_the_last_upgrade(tmp_path):
    from labdex.agent import patch_report as pr
    log = tmp_path / "history.log"
    log.write_text("Start-Date: 2026-11-10  06:00:01\nCommandline: apt-get upgrade\nUpgrade: curl:amd64 (8.5.0-2, 8.5.0-3)\nEnd-Date: 2026-11-10  06:00:09\n\n"
                   "Start-Date: 2026-11-12  10:00:00\nInstall: htop:amd64 (3.3.0-4)\nEnd-Date: 2026-11-12  10:00:02\n")
    assert pr.apt_patched_at(str(log)).startswith("2026-11-10T06:00:01")
    assert pr.apt_patched_at(str(tmp_path / "missing")) is None


def test_agent_script_is_served_and_its_report_is_taken(admin, monkeypatch):
    r = admin.get("/api/patches/agent")
    assert r.status_code == 200 and b"labdex-patch-report" in r.data
    r.close()
    from labdex.agent import patch_report as pr
    report = {"host": DNS, "source": "agent", "os": "Ubuntu 24.04.1 LTS", "kernel": "6.8.0-45-generic",
              "packages": pr.parse_apt(APT), "reboot": False, "patched_at": "2026-11-10T06:00:01+01:00", "pending": 3}
    assert admin.post("/api/patches", json=report).get_json()["saved"] == [DNS]
    s = patches.summary(DNS)
    assert s["state"] == "security" and s["pending"] == 3 and s["patched_at"] == "2026-11-10T05:00:01Z"
