"""v0.5.9: services: description, where they listen, link by address, allow-from checks, overview data."""
from labdex import discovery
from labdex.models import db

from .test_v03 import host, put_host


def svc(h, name):
    return next(s for s in h["services"] if s["name"] == name)


def test_bind_link_and_description(admin):
    h = host(admin, "srv-vm-mon-01")
    s = svc(h, "grafana")
    s.update(description="Dashboards", bind="10.10.100.20", link_to="ip")
    h = put_host(admin, "srv-vm-mon-01", services=h["services"])
    s = svc(h, "grafana")
    assert s["description"] == "Dashboards" and s["addresses"] == ["10.10.100.20"]
    assert next(link["url"] for link in h["links"] if link["name"] == "grafana") == "https://10.10.100.20"
    # bind by network and 'primary'
    s.update(bind="srv", link_to="")
    h = put_host(admin, "srv-vm-mon-01", services=h["services"])
    assert "10.10.100.20" in svc(h, "grafana")["addresses"]
    s = svc(h, "grafana")
    s["bind"] = "10.99.0.1"
    r = admin.put("/api/hosts/srv-vm-mon-01", json=h)
    assert r.status_code == 422 and "services[0].bind" in str(r.get_json())
    s["bind"], s["link_to"] = "", "telnet"
    r = admin.put("/api/hosts/srv-vm-mon-01", json=h)
    assert r.status_code == 422 and "link_to" in str(r.get_json())


def test_services_overview_status_and_sources(admin):
    h = host(admin, "srv-vm-dns-01")
    for s in h["services"]:
        s["bind"] = "10.10.100.53"
    put_host(admin, "srv-vm-dns-01", services=h["services"])
    h = host(admin, "srv-vm-ha-01")
    h["services"][0]["bind"] = "10.10.100.30"
    put_host(admin, "srv-vm-ha-01", services=h["services"])
    # 5380/tcp open on dns' bound address; Home Assistant's port open only on an address it isn't bound to
    ha_port = h["services"][0]["port"]
    discovery.record_scan("nmap:x", {"10.10.100.53", "10.10.100.30", "10.10.201.30"}, [
        {"ip": "10.10.100.53", "services": [{"port": 5380, "proto": "tcp", "service": "http"}]},
        {"ip": "10.10.100.30", "services": [{"port": 22, "proto": "tcp", "service": "ssh"}]},
        {"ip": "10.10.201.30", "services": [{"port": ha_port, "proto": "tcp", "service": "http"}]}],
        probed={"10.10.100.53": [[5380, "tcp"]], "10.10.100.30": [[ha_port, "tcp"]]})  # the declared ports, checked on purpose
    db.session.commit()
    rows = {(s["host"], s["name"]): s for s in admin.get("/api/services").get_json()}
    assert rows[("srv-vm-dns-01", "dns-admin")]["status"] == "open"
    assert rows[("srv-vm-ha-01", h["services"][0]["name"])]["status"] == "closed"  # answering only where it shouldn't
    assert rows[("srv-vm-dns-01", "dns")]["status"] == "unknown"  # UDP: nmap doesn't look
    assert rows[("srv-vm-mon-01", "grafana")]["status"] == "unknown"
    # "declared, not seen" only looks at the bound address
    missing = {(m["host"], m["service"]) for m in discovery.missing()}
    assert ("srv-vm-ha-01", h["services"][0]["name"]) in missing
    src = admin.get("/api/services/sources").get_json()
    assert "DC_TC" in src["zones"] and "DC_*" in src["areas"] and "srv" in src["networks"] and src["special"] == ["any"]


def test_allow_from_unknown_is_flagged(admin):
    h = host(admin, "srv-vm-dns-01")
    h["services"][0]["allow_from"] = ["CA_*", "mgmt", "10.0.0.0/8", "any", "nonsense"]
    put_host(admin, "srv-vm-dns-01", services=h["services"])
    issues = [i for i in admin.get("/api/lint").get_json()["issues"] if i["code"] == "allow-from-unknown"]
    assert len(issues) == 1 and "nonsense" in issues[0]["message"]
