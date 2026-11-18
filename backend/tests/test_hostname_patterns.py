"""Hostname patterns: a lab with its own naming scheme lists it, and the linter accepts those names."""
from labdex import lint, naming


def _hostname_findings():
    return {i["ref"] for i in lint.run() if i["code"] == "hostname"}


def test_default_pattern():
    rx = naming.compile_hostname_patterns("{category}-{type}-{role}-{nn}", ["srv"])
    assert rx.match("srv-vm-dns-01")
    assert not rx.match("srv-vm-europa") and not rx.match("xyz-vm-dns-01")


def test_several_patterns_and_tokens():
    rx = naming.compile_hostname_patterns("{category}-{type}-{name}\n{category}-{type}-{role}-{nn}", ["int", "srv"])
    for ok in ("int-vm-europa", "int-pve-mars", "srv-vm-dns-01", "int-vps-eiger-cluster-oak"):
        assert rx.match(ok), ok
    for bad in ("ext-vm-europa", "int-vm", "Int-vm-europa"):
        assert not rx.match(bad), bad


def test_bad_patterns_are_refused():
    assert naming.check_hostname_patterns("{category}-{nn}") == ""
    assert "not a pattern" in naming.check_hostname_patterns("{whatever}-{nn}")
    assert "not a pattern" in naming.check_hostname_patterns("srv.vm")
    assert "at least one" in naming.check_hostname_patterns("  ")


def test_setting_drives_the_linter(admin):
    admin.post("/api/networks", json={"name": "lab", "zone": "DC_TC", "prefix4": "192.0.2.0/24"})
    r = admin.post("/api/hosts", json={"name": "srv-vm-europa", "zone": "DC_TC",
                                       "interfaces": [{"network": "lab", "ip4": "192.0.2.10"}]})
    assert r.status_code == 201, r.get_json()
    assert "srv-vm-europa" in _hostname_findings()

    r = admin.put("/api/admin/settings", json={"hostname_patterns": "{nope}"})
    assert r.status_code == 422 and r.get_json()["errors"][0]["field"] == "hostname_patterns"

    r = admin.put("/api/admin/settings", json={"hostname_patterns": " {category}-{type}-{name} \n\n{category}-{type}-{role}-{nn}"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["hostname_patterns"] == "{category}-{type}-{name}\n{category}-{type}-{role}-{nn}"
    assert "srv-vm-europa" not in _hostname_findings()
