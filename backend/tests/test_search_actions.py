"""Ctrl K: "verb host" offers actions on those hosts, as plans, before everything else."""
from labdex import patches
from tests.test_patching_plugin import DNS, patching  # noqa: F401  (the fixture)


def results(c, q):
    return c.get("/api/search", query_string={"q": q}).get_json()["results"]


def test_patch_a_host_comes_first_with_its_patch_state(patching):  # noqa: F811
    patches.report("agent", {DNS: {"packages": [{"name": "openssl", "security": True}, "curl"], "reboot": True}})
    r = results(patching, "patch srv-vm-dns")
    first = r[0]
    assert first["kind"] == "action" and first["title"] == f"Plan install updates on {DNS}"
    assert first["params"] == {"hosts": [DNS]} and first["mode"] == "plan"
    assert "1 security update" in first["sub"] and "reboot needed" in first["sub"]


def test_a_prefix_offers_all_matching_hosts(patching):  # noqa: F811
    r = [x for x in results(patching, "install srv-vm*") if x["kind"] == "action"]
    many = [x for x in r if " on all " in x["title"]]
    assert many and all(h.startswith("srv-vm") for h in many[0]["params"]["hosts"]) and len(many[0]["params"]["hosts"]) > 1


def test_no_actions_without_a_matching_host_or_verb(patching):  # noqa: F811
    assert not [x for x in results(patching, "patch nosuchhost") if x["kind"] == "action"]
    assert not [x for x in results(patching, f"frobnicate {DNS}") if x["kind"] == "action"]


def test_viewers_get_no_actions(patching, make_user):  # noqa: F811
    viewer = make_user("vic", role="viewer")
    assert not [x for x in results(viewer, f"patch {DNS}") if x["kind"] == "action"]


def test_host_detail_lists_its_plan_actions(patching, make_user):  # noqa: F811
    acts = patching.get(f"/api/hosts/{DNS}").get_json()["plan_actions"]
    assert {"integration": "home-patching", "plugin": "patching", "action": "patch", "label": "Install updates",
            "params": {"hosts": [DNS]}} in acts
    viewer = make_user("vic", role="viewer")
    assert viewer.get(f"/api/hosts/{DNS}").get_json()["plan_actions"] == []


def test_plan_lists_the_plans_you_may_make(patching):  # noqa: F811
    r = [x for x in results(patching, "plan") if x["kind"] == "action"]
    assert r and all(x["mode"] == "plan" for x in r)
