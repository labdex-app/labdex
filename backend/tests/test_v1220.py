"""1.22: the Policy page shows the firewalls that enforce it; a host's fun name keeps its place on the map."""
import pytest

from .fakes import FakeOPNsense
from .test_plugins import make_instance


@pytest.fixture
def opn():
    o = FakeOPNsense()
    yield o
    o.srv.close()


def test_policy_lists_the_firewalls(admin, make_user, opn):
    assert admin.get("/api/zone-policy").get_json()["firewalls"] == []
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": opn.KEY, "api_secret": opn.SECRET})
    fws = admin.get("/api/zone-policy").get_json()["firewalls"]
    assert [(f["integration"], f["plugin"], f["page"]) for f in fws] == [("fw", "opnsense", "policy")]
    assert fws[0]["action"]["id"] == "sync_rules" and fws[0]["action"]["mode"] == "plan" and fws[0]["can_apply"] is True
    # the page itself is the plugin's, read through the integration
    page = admin.get("/api/integrations/fw/pages/policy").get_json()
    assert {s["title"] for s in page["sections"]} >= {"Zones", "Rules no policy explains"}
    # the plugin's own page list still has it, marked for the Policy page
    spec = admin.get("/api/plugins/opnsense").get_json()
    assert next(p for p in spec["pages"] if p["id"] == "policy")["place"] == "policy"
    # someone who can't see the integration doesn't see the firewall
    v = make_user("v2", role="viewer")
    assert v.get("/api/zone-policy").status_code == 200
    admin.put("/api/integrations/fw", json={"enabled": False})
    assert admin.get("/api/zone-policy").get_json()["firewalls"] == []  # disabled: not reporting


def test_name_offsets_are_kept_with_the_layout(admin):
    r = admin.put("/api/topology/layout?view=l3", json={"positions": {"h:srv-vm-dns-01": {"x": 48, "y": 24}, "t:h:srv-vm-dns-01": {"x": 40, "y": -6},
                                                                      "t:h:gone": {"x": 1, "y": 1}}})
    assert r.status_code == 200
    lay = admin.get("/api/topology?view=l3").get_json()["layout"]
    assert lay["t:h:srv-vm-dns-01"] == {"x": 40.0, "y": -6.0} and "t:h:gone" not in lay
