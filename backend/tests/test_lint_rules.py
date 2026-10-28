"""Lint rules for prefixes, DHCP ranges and service ports, and accepting a finding on purpose."""
from labdex import cache, lint
from labdex.models import Host, Network, NetworkPrefix, Service, db


def net(name):
    return db.session.scalar(db.select(Network).where(Network.name == name))


def host(name):
    return db.session.scalar(db.select(Host).where(Host.name == name))


def codes(ref=None):
    return {i["code"] for i in lint.run() if ref is None or i["ref"] == ref}


def test_overlapping_prefixes(loaded):
    a, b = net("mgmt"), net("k8s")
    b.prefixes.append(NetworkPrefix(family=4, prefix=a.prefix4, position=9))
    db.session.flush()
    cache.bump()
    hits = [i for i in lint.run() if i["code"] == "prefix-overlap"]
    assert {i["ref"] for i in hits} == {"mgmt", "k8s"} and all(i["severity"] == "error" for i in hits)
    db.session.rollback()


def test_gateway_and_dhcp_range(loaded):
    n = net("mgmt")
    p = next(x for x in n.prefixes if x.family == 4)
    p.gateway = "192.168.99.1"
    p.dhcp_start, p.dhcp_end = "10.10.3.200", "10.10.3.100"
    db.session.flush()
    cache.bump()
    c = codes("mgmt")
    assert {"gateway-outside", "dhcp-range-order"} <= c
    p.dhcp_start, p.dhcp_end = "10.10.2.250", "10.10.3.20"
    cache.bump()
    assert "dhcp-range-outside" in codes("mgmt")
    db.session.rollback()


def test_service_port_clash(loaded):
    h = host("srv-vm-k3s-01")
    h.services.append(Service(name="web-a", port=8080, proto="tcp"))
    h.services.append(Service(name="web-b", port=8080, proto="tcp"))
    db.session.flush()
    cache.bump()
    assert "service-port-clash" in codes(h.name)
    for s in h.services:
        s.bind = {"web-a": "eth0", "web-b": "eth1"}.get(s.name, s.bind)
    cache.bump()
    assert "service-port-clash" not in codes(h.name)
    db.session.rollback()


def test_example_is_still_clean(loaded):
    s = lint.summary(lint.run())
    assert s["error"] == 0 and s["warning"] == 0, lint.run()


def test_accepting_a_finding(admin):
    h = host("srv-vm-k3s-02")
    h.interfaces[0].ip4 = host("srv-vm-k3s-01").interfaces[0].ip4
    db.session.commit()
    first = [i for i in lint.run() if i["code"] == "duplicate-ip"]
    assert first
    f = first[0]
    key = {"code": f["code"], "kind": f["kind"], "ref": f["ref"]}

    assert admin.post("/api/lint/accept", json=key).status_code == 422  # a reason is required
    r = admin.post("/api/lint/accept", json={**key, "reason": "cluster VIP, on purpose"})
    assert r.status_code == 200, r.get_json()

    assert not any(i["code"] == "duplicate-ip" for i in lint.run())
    shown = [i for i in admin.get("/api/lint").get_json()["issues"] if i["code"] == "duplicate-ip"]
    assert shown and shown[0]["accepted"] == "cluster VIP, on purpose"
    assert admin.get("/api/lint").get_json()["summary"]["error"] == 0

    assert admin.delete("/api/lint/accept", json=key).status_code == 200
    assert any(i["code"] == "duplicate-ip" for i in lint.run())


def test_only_admins_accept(loaded, make_user):
    c = make_user("ed", role="editor")
    r = c.post("/api/lint/accept", json={"code": "hostname", "kind": "host", "ref": "x", "reason": "no"})
    assert r.status_code == 403
