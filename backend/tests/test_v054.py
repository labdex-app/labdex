"""v0.5.4: network page data for the Hosts tab."""


def test_network_members(admin, make_user):
    n = admin.get("/api/networks/srv").get_json()
    names = [m["name"] for m in n["members"]]
    assert "srv-vm-dns-01" in names and "net-fw-edge-01" in names and names == sorted(names)
    dns = next(m for m in n["members"] if m["name"] == "srv-vm-dns-01")
    assert dns["addresses"][0]["ip4"] == "10.10.100.53" and dns["function"] == "DNS resolver"
    # members are filtered by what the caller may see
    v = make_user("viewer-dmz", role="viewer", scopes=["zone:DC_TC"])
    seen = {m["name"] for m in v.get("/api/networks/srv").get_json()["members"]}
    assert "srv-vm-dns-01" in seen
