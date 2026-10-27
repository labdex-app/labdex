"""1.10: topology views and cables drawn on the map."""
from .test_v03 import host


def test_l3_view_joins_hosts_to_networks(admin):
    t = admin.get("/api/topology?view=l3").get_json()
    assert t["view"] == "l3"
    nodes = {n["id"]: n for n in t["nodes"]}
    assert nodes["n:srv"]["type"] == "network" and nodes["n:srv"]["prefix"] == "10.10.100.0/24"
    dns = host(admin, "srv-vm-dns-01")
    ip = dns["interfaces"][0]["ip4"]
    assert any(e["source"] == "h:srv-vm-dns-01" and e["target"] == "n:srv" and e["label"] == ip for e in t["edges"])
    assert any(n.get("router") for n in t["nodes"] if n["type"] == "host")  # the firewall sits in several
    # its own arrangement, apart from the physical one
    admin.put("/api/topology/layout?view=l3", json={"positions": {"n:srv": {"x": 1, "y": 2}}})
    assert admin.get("/api/topology?view=l3").get_json()["layout"] == {"n:srv": {"x": 1.0, "y": 2.0}}
    assert admin.get("/api/topology").get_json()["layout"] == {}
    z = admin.get(f"/api/topology?view=l3&zone={nodes['n:srv']['zone']}").get_json()
    assert any(n["id"] == "n:srv" for n in z["nodes"])


def test_l3_respects_rights(admin, make_user):
    v = make_user("v", role="viewer", scopes=["zone:CA_*"])
    t = v.get("/api/topology?view=l3").get_json()
    assert not any(n["id"] == "n:srv" for n in t["nodes"])


def test_draw_a_cable_adds_missing_ports(admin):
    r = admin.post("/api/topology/cables", json={"a": {"host": "srv-vm-dns-01", "port": "eth7"},
                                                "b": {"host": "net-sw-core-01", "port": "ge-0/0/30"}, "medium": "fiber", "speed": "10G"})
    assert r.status_code == 201, r.get_json()
    dns, sw = host(admin, "srv-vm-dns-01"), host(admin, "net-sw-core-01")
    assert any(i["label"] == "eth7" for i in dns["interfaces"]) and any(i["label"] == "ge-0/0/30" for i in sw["interfaces"])
    c = next(c for c in dns["cables"] if c["port"] == "eth7")
    assert c == {"port": "eth7", "peer": "net-sw-core-01", "peer_port": "ge-0/0/30", "medium": "fiber", "speed": "10G", "notes": "", "label": "", "length": "", "colour": ""}
    assert any(c["peer"] == "srv-vm-dns-01" for c in sw["cables"])
    hist = admin.get("/api/history?object_name=net-sw-core-01").get_json()
    assert "cable srv-vm-dns-01 eth7" in hist[0]["summary"]
    edge = next(e for e in admin.get("/api/topology").get_json()["edges"] if e.get("a_port") == "eth7" or e.get("b_port") == "eth7")
    assert edge["speed"] == "10G"
    assert admin.delete(f"/api/topology/cables/{edge['id'][2:]}").status_code == 204
    assert not any(c["port"] == "eth7" for c in host(admin, "srv-vm-dns-01")["cables"])
    assert admin.delete(f"/api/topology/cables/{edge['id'][2:]}").status_code == 404


def test_draw_a_cable_refuses_nonsense(admin, make_user):
    bad = [({"a": {"host": "nope", "port": "x"}, "b": {"host": "srv-vm-dns-01", "port": "y"}}, "a.host"),
           ({"a": {"host": "srv-vm-dns-01", "port": ""}, "b": {"host": "net-sw-core-01", "port": "y"}}, "a.port"),
           ({"a": {"host": "srv-vm-dns-01", "port": "x"}, "b": {"host": "srv-vm-dns-01", "port": "x"}}, "b.port"),
           ({"a": {"host": "srv-vm-dns-01", "port": "x"}, "b": {"host": "net-sw-core-01", "port": "y"}, "medium": "laser"}, "cables[")]
    for payload, field in bad:
        r = admin.post("/api/topology/cables", json=payload)
        assert r.status_code == 422 and field in str(r.get_json()), (payload, r.get_json())
    v = make_user("v", role="viewer")
    r = v.post("/api/topology/cables", json={"a": {"host": "srv-vm-dns-01", "port": "x"}, "b": {"host": "net-sw-core-01", "port": "y"}})
    assert r.status_code == 403


def test_draw_a_cable_names_an_unnamed_interface(admin):
    dns = host(admin, "srv-vm-mon-01")
    idx = next(n for n, i in enumerate(dns["interfaces"]) if not i["label"])
    net = dns["interfaces"][idx]["network"]
    r = admin.post("/api/topology/cables", json={"a": {"host": "srv-vm-mon-01", "port": "eth0", "iface": idx},
                                                "b": {"host": "net-sw-core-01", "port": "ge-0/0/31"}})
    assert r.status_code == 201, r.get_json()
    after = host(admin, "srv-vm-mon-01")
    assert after["interfaces"][idx]["label"] == "eth0" and after["interfaces"][idx]["network"] == net
    assert len(after["interfaces"]) == len(dns["interfaces"])


# ------------------------------------------------- declared ports are really checked
def test_probed_ports_decide_answering(admin):
    from labdex import discovery
    from labdex.models import db
    h = host(admin, "srv-vm-mon-01")
    ip = next(i["ip4"] for i in h["interfaces"] if i["ip4"])
    port = next(s["port"] for s in h["services"] if s["port"] and (s["proto"] or "tcp") == "tcp")
    name = next(s["name"] for s in h["services"] if s["port"] == port)
    discovery.record_scan("nmap:x", {ip}, [{"ip": ip, "services": [{"port": 22, "proto": "tcp"}]}])
    db.session.commit()
    row = next(s for s in admin.get("/api/services").get_json() if s["host"] == "srv-vm-mon-01" and s["name"] == name)
    assert row["status"] == "unknown"  # scanned, but nobody looked at this port: not "not answering"
    assert not any(m["service"] == name for m in discovery.missing())
    discovery.record_scan("nmap:x", {ip}, [{"ip": ip, "services": [{"port": 22, "proto": "tcp"}]}], probed={ip: [[port, "tcp"]]})
    db.session.commit()
    row = next(s for s in admin.get("/api/services").get_json() if s["host"] == "srv-vm-mon-01" and s["name"] == name)
    assert row["status"] == "closed" and any(m["service"] == name for m in discovery.missing())


def test_nmap_checks_declared_ports_outside_its_list(admin):
    import http.server
    import shutil
    import threading
    import pytest
    from .test_plugins import _free_port, make_instance, run
    if not shutil.which("nmap"):
        pytest.skip("nmap not installed")
    port = _free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        admin.post("/api/networks", json={"name": "lo", "zone": "DC_TC", "prefix4": "127.0.0.0/30", "in_dns": False})
        admin.post("/api/hosts", json={"name": "srv-vm-lo-01", "zone": "DC_TC", "interfaces": [{"network": "lo", "ip4": "127.0.0.1"}],
                                       "services": [{"name": "plex", "port": port}, {"name": "ghost", "port": _free_port()}]})
        make_instance(admin, "nmap", "scan", {"networks": ["lo"], "ports": "1", "service_detection": False, "timing": "T4", "ipv6": False})
        job = run(admin, "scan", "scan")
        assert job["status"] == "succeeded", job
        rows = {s["name"]: s for s in admin.get("/api/services").get_json() if s["host"] == "srv-vm-lo-01"}
        assert rows["plex"]["status"] == "open" and rows["ghost"]["status"] == "closed"
    finally:
        httpd.shutdown()


# ------------------------------------------------------------- zone codes as names
def test_zone_codes_are_names():
    from labdex.zones import clean_code
    assert clean_code("Trusted clients") == "TRUSTED_CLIENTS"
    assert clean_code(" iot - cameras. ") == "IOT_CAMERAS"
    assert clean_code("Home Lab", area=True) == "HOMELAB"
    assert clean_code("über!") == "BER"


def test_long_zones_everywhere(admin):
    r = admin.post("/api/zones/areas", json={"code": "Home Lab", "name": "At home"})
    assert r.status_code in (200, 201), r.get_json()
    r = admin.post("/api/zones/zones", json={"area": "HOMELAB", "code": "HOMELAB_trusted clients", "name": "Trusted"})
    assert r.status_code in (200, 201), r.get_json()
    code = "HOMELAB_TRUSTED_CLIENTS"
    assert any(z["code"] == code for z in admin.get("/api/meta").get_json()["zones"])
    r = admin.post("/api/networks", json={"name": "home", "zone": code, "prefix4": "192.168.77.0/24", "vlan": 77})
    assert r.status_code == 201, r.get_json()
    r = admin.post("/api/hosts", json={"name": "srv-vm-home-01", "zone": code, "interfaces": [{"network": "home", "ip4": "192.168.77.5"}]})
    assert r.status_code == 201, r.get_json()
    assert host(admin, "srv-vm-home-01")["zone"] == code
    r = admin.post("/api/zones/zones", json={"area": "HOMELAB", "code": "HOMELAB_" + "X" * 60})
    assert r.status_code == 422
    r = admin.post("/api/zones/areas", json={"code": "A" * 17, "name": "too long"})
    assert r.status_code == 422
