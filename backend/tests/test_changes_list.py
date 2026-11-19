"""The Changes page lists plans with what they add, change and remove, and their site."""
from .fakes import FakeOPNsense
from .test_plugins import make_instance, run


def test_plan_list_has_ops_and_site(admin):
    fw = FakeOPNsense()
    try:
        make_instance(admin, "opnsense", "fw", {"url": fw.url, "api_key": "k", "api_secret": "s", "verify_tls": False,
                                                "kea_reservations": True})
        plan = run(admin, "fw", "sync_reservations")
        assert plan["status"] == "planned"
        row = next(j for j in admin.get("/api/jobs?status=planned").get_json() if j["id"] == plan["id"])
        assert row["ops"] == {"create": len(plan["plan"])} and row["site"] == "home"
        assert row["changes"] == len(plan["plan"])
    finally:
        fw.srv.close()


def test_dashboard_sites_and_updates(admin):
    d = admin.get("/api/dashboard").get_json()
    home = next(s for s in d["sites"] if s["code"] == "home")
    assert home["hosts"] == d["counts"]["hosts"] and home["git"] is None and home["open_plans"] == 0
    assert set(d["counts"]["updates"]) == {"hosts", "security", "reboot"}
