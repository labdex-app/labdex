"""Importing from CSV, NetBox and phpIPAM: what's added, what's skipped, previews keep nothing."""
import pytest
from flask.testing import FlaskCliRunner

from labdex import importers, naming, store, zones
from labdex.models import AuditEvent, Host, Network, db


@pytest.fixture
def blank(app):
    zones.load_example()
    db.session.commit()
    return app


def hosts():
    return {h.name: h for h in db.session.scalars(db.select(Host))}


def nets():
    return {n.name: n for n in db.session.scalars(db.select(Network))}


NETBOX = {
    "prefixes": [
        {"prefix": "10.20.0.0/24", "vlan": {"vid": 20, "name": "Servers"}, "description": "rack servers"},
        {"prefix": "10.30.0.0/24", "vlan": None, "description": "Lab Net"},
        {"prefix": "fd00::/64", "vlan": None, "description": "v6"},
    ],
    "devices": [
        {"name": "Core-Switch.lab.example", "role": {"name": "Switch"}, "device_type": {"model": "CRS326", "manufacturer": {"name": "MikroTik"}},
         "status": {"value": "active"}, "primary_ip4": {"address": "10.20.0.2/24"}, "tags": [{"slug": "network"}], "comments": "top of rack"},
        {"name": "old box", "status": {"value": "decommissioning"}, "primary_ip4": None, "tags": []},
    ],
    "virtual_machines": [{"name": "web01", "status": {"value": "active"}, "primary_ip4": {"address": "10.20.0.50/24"}, "tags": [], "vcpus": 2}],
    "ip_addresses": [
        {"address": "10.20.0.2/24", "assigned_object": {"name": "mgmt0", "device": {"name": "Core-Switch.lab.example"}}},
        {"address": "10.30.0.7/24", "assigned_object": {"name": "eth1", "device": {"name": "Core-Switch.lab.example"}}},
        {"address": "10.20.0.50/24", "assigned_object": {"name": "eth0", "virtual_machine": {"name": "web01"}}},
        {"address": "fd00::5/64", "assigned_object": {"name": "eth0", "virtual_machine": {"name": "web01"}}},
        {"address": "10.99.0.1/24", "assigned_object": None},
    ],
    "interfaces": [{"name": "mgmt0", "device": {"name": "Core-Switch.lab.example"}, "mac_address": "AA:BB:CC:00:00:01"}],
}


def test_netbox_plan():
    plan = importers.from_netbox(NETBOX)
    assert [n["name"] for n in plan["networks"]] == ["servers", "lab-net"]
    assert plan["networks"][0]["vlan"] == 20 and plan["networks"][0]["prefix4"] == "10.20.0.0/24"
    by = {h["name"]: h for h in plan["hosts"]}
    assert set(by) == {"core-switch", "old-box", "web01"}
    sw = by["core-switch"]
    assert sw["vendor"] == "MikroTik" and sw["model"] == "CRS326" and sw["function"] == "Switch" and sw["tags"] == ["network"]
    assert "NetBox name: Core-Switch.lab.example" in sw["notes"] and "top of rack" in sw["notes"]
    assert {(i["ip4"], i.get("mac"), i["primary"]) for i in sw["interfaces"]} == {("10.20.0.2", "aa:bb:cc:00:00:01", True), ("10.30.0.7", None, False)}
    assert by["old-box"]["status"] == "legacy"
    assert any("IPv6 prefix" in w for w in plan["warnings"]) and any("IPv6 address" in w for w in plan["warnings"])


def test_preview_keeps_nothing_then_apply(blank):
    plan = importers.from_netbox(NETBOX)
    out = importers.run(plan, "DC_TC")
    assert out["created"] == {"networks": ["servers", "lab-net"], "hosts": ["core-switch", "old-box", "web01"]} and not out["failed"]
    assert not hosts() and not nets()                                   # a preview keeps nothing

    out = importers.run(plan, "DC_TC", {"servers": "DC_HT"}, apply=True)
    assert set(hosts()) == {"core-switch", "old-box", "web01"} and set(nets()) == {"servers", "lab-net"}
    assert nets()["servers"].zone == "DC_HT" and nets()["lab-net"].zone == "DC_TC" and nets()["servers"].vlan == 20
    sw = hosts()["core-switch"]
    assert sw.zone == "DC_HT"                                            # follows its primary interface's network
    assert {i.network.name for i in sw.interfaces} == {"servers", "lab-net"} and sw.vendor == "MikroTik"
    assert db.session.scalar(db.select(db.func.count()).select_from(AuditEvent).where(AuditEvent.summary == "imported")) == 5

    again = importers.run(plan, "DC_TC", apply=True)                      # nothing is overwritten or duplicated
    assert not again["created"]["networks"] and not again["created"]["hosts"]
    assert {s["name"] for s in again["skipped"]} == {"servers", "lab-net", "core-switch", "old-box", "web01"}


def test_unknown_zone_is_explained(app):
    with pytest.raises(store.ValidationError) as e:
        importers.run({"networks": [], "hosts": []}, "DC_TC")
    assert "Settings" in e.value.errors[0]["message"]                  # no zones at all yet
    zones.load_example()
    with pytest.raises(store.ValidationError):
        importers.run({"networks": [], "hosts": []}, "NOPE")
    with pytest.raises(store.ValidationError):
        importers.run({"networks": [], "hosts": []}, "DC_TC", {"x": "NOPE"})


def test_addresses_outside_known_networks_and_taken_ones(blank):
    plan = {"networks": [{"name": "srv", "prefix4": "10.1.0.0/24"}], "warnings": [],
            "hosts": [{"name": "a-host", "interfaces": [{"ip4": "10.1.0.5"}, {"ip4": "192.168.5.5"}]},
                      {"name": "b-host", "interfaces": [{"ip4": "10.1.0.5"}]}]}
    out = importers.run(plan, "DC_TC", apply=True)
    assert out["created"]["hosts"] == ["a-host", "b-host"]
    assert any("192.168.5.5" in w for w in out["warnings"]) and any("already used" in w for w in out["warnings"])
    assert [i.ip4 for i in hosts()["a-host"].interfaces] == ["10.1.0.5"] and not hosts()["b-host"].interfaces


def test_existing_network_matched_by_prefix_and_name_clash(blank):
    store.save_network({"name": "srv", "zone": "DC_TC", "prefix4": "10.1.0.0/24", "vlan": 100})
    db.session.commit()
    plan = {"networks": [{"name": "other", "prefix4": "10.1.0.0/24"},                         # same prefix: skipped
                         {"name": "srv", "prefix4": "10.2.0.0/24", "vlan": 100}], "hosts": [], "warnings": []}  # same name and VLAN
    out = importers.run(plan, "DC_TC", apply=True)
    assert out["skipped"][0]["name"] == "other"
    assert out["created"]["networks"] == ["srv-2"] and nets()["srv-2"].vlan is None
    assert any("VLAN 100" in w for w in out["warnings"])


def test_csv_hosts_and_networks():
    nets_csv = "Name;Prefix;VLAN;Gateway;Zone\nservers;10.5.0.0/24;50;10.5.0.1;DC_TC\n;;;;\nbad;;;;\n"
    hosts_csv = ("name,ip,mac,network,status,tags,function,color\n"
                 "web-1.lab,10.5.0.10,AA:BB:CC:DD:EE:01,,Active,\"web, prod\",Web server,red\n"
                 "web-1.lab,10.5.0.11,,,,,,\n"
                 "nas,fd00::1,,,planned,,,\n")
    n = importers.from_csv(nets_csv, "networks")
    assert n["networks"] == [{"name": "servers", "prefix4": "10.5.0.0/24", "notes": "", "vlan": "50", "gateway4": "10.5.0.1", "zone": "DC_TC"}]
    assert any("no prefix" in w for w in n["warnings"])
    h = importers.from_csv(hosts_csv)
    by = {x["name"]: x for x in h["hosts"]}
    assert by["web-1"]["tags"] == ["prod", "web"] and by["web-1"]["status"] == "active" and by["web-1"]["function"] == "Web server"
    assert [i["ip4"] for i in by["web-1"]["interfaces"]] == ["10.5.0.10", "10.5.0.11"] and by["web-1"]["interfaces"][0]["mac"] == "AA:BB:CC:DD:EE:01"
    assert by["nas"]["interfaces"] == [] and any("isn't an IPv4" in w for w in h["warnings"]) and any("color" in w for w in h["warnings"])


def test_phpipam_plan():
    plan = importers.from_phpipam({
        "subnets": [{"id": "3", "subnet": "10.7.0.0", "mask": "24", "description": "Cameras", "vlanId": "9"},
                    {"id": "4", "subnet": "10.8.0.0", "mask": "16", "isFolder": "1"},
                    {"id": "5", "subnet": "fd00::", "mask": "64"}],
        "vlans": [{"vlanId": "9", "number": "70", "name": "cams"}],
        "addresses": {"3": [{"ip": "10.7.0.4", "hostname": "cam-front.lab", "mac": "aa-bb-cc-00-11-22", "description": "front door"},
                            {"ip": "10.7.0.5", "hostname": ""}]},
    })
    assert plan["networks"] == [{"name": "cams", "prefix4": "10.7.0.0/24", "notes": "Cameras", "vlan": 70}]
    assert plan["hosts"][0]["name"] == "cam-front" and plan["hosts"][0]["interfaces"] == [{"ip4": "10.7.0.4", "mac": "aa:bb:cc:00:11:22"}]
    assert plan["hosts"][0]["function"] == "front door" and len(plan["hosts"]) == 1


def test_fetch_netbox_pages_and_refused_token():
    class Resp:
        def __init__(self, status, body):
            self.status_code, self._b = status, body

        def json(self):
            return self._b

        def raise_for_status(self):
            assert self.status_code < 400

    class Sess:
        def __init__(self, status=200):
            self.headers, self.urls, self.status = {}, [], status

        def get(self, url, timeout, verify):
            self.urls.append(url)
            if url.endswith("limit=500") and "prefixes" in url:
                return Resp(self.status, {"results": [{"prefix": "10.0.0.0/8"}], "next": url + "&offset=500"})
            return Resp(self.status, {"results": [], "next": None})

    s = Sess()
    out = importers.fetch_netbox("https://nb.example/", "tok", session=s)
    assert out["prefixes"] == [{"prefix": "10.0.0.0/8"}] * 1 + [] and s.headers["Authorization"] == "Token tok"
    assert any(u.endswith("offset=500") for u in s.urls)
    with pytest.raises(store.ValidationError):
        importers.fetch_netbox("https://nb.example", "bad", session=Sess(401))


def test_admin_api(admin, make_user):
    body = {"source": "csv", "zone": "DC_TC", "networks_text": "name,prefix\nlab,10.9.0.0/24\n", "hosts_text": "name,ip\nbox,10.9.0.4\n"}
    r = admin.post("/api/admin/import/external", json=body)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["applied"] is False and d["created"] == {"networks": ["lab"], "hosts": ["box"]} and admin.get("/api/hosts/box").status_code == 404
    r = admin.post("/api/admin/import/external", json=body | {"apply": True})
    assert r.get_json()["applied"] and admin.get("/api/hosts/box").status_code == 200
    ev = admin.get("/api/history?object_type=inventory&action=import").get_json()
    assert ev and "added from csv" in ev[0]["summary"]
    assert admin.post("/api/admin/import/external", json={"source": "nope"}).status_code == 422
    assert admin.post("/api/admin/import/external", json={"source": "netbox", "zone": "DC_TC"}).status_code == 422
    assert admin.post("/api/admin/import/external", json=body | {"zone": "XX"}).status_code == 422
    ed = make_user("ed", role="editor")
    assert ed.post("/api/admin/import/external", json=body).status_code == 403


def test_cli_preview_and_apply(blank, tmp_path):
    f = tmp_path / "hosts.csv"
    f.write_text("name,ip\nbox,10.9.0.4\n", encoding="utf-8")
    n = tmp_path / "nets.csv"
    n.write_text("name,prefix\nlab,10.9.0.0/24\n", encoding="utf-8")
    runner = FlaskCliRunner(blank)
    args = ["import-from", "csv", "--zone", "DC_TC", "--hosts", str(f), "--networks", str(n)]
    out = runner.invoke(args=args)
    assert out.exit_code == 0 and "would add" in out.output and "preview" in out.output and not hosts()
    out = runner.invoke(args=[*args, "--apply"])
    assert out.exit_code == 0 and "added" in out.output and "box" in hosts()
    assert runner.invoke(args=["import-from", "csv", "--zone", "NOPE", "--hosts", str(f)]).exit_code != 0
    assert naming.parse_zone("DC_TC")
