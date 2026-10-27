"""1.20: cables that switches see (LLDP/CDP, UniFi), suggested against the inventory."""
from labdex import cabling
from labdex.models import db

from .fakes import FakeServer, FakeUniFiOS
from .test_plugins import make_instance, run
from .test_v03 import host, put_host


def seen(source, *links):
    out = cabling.record(source, list(links))
    db.session.commit()
    return out


def lk(port, remote, remote_port="", protocol="lldp", **dev):
    return {"device": dev or {"name": "core"}, "host": dev.pop("host", None) if dev else None, "port": port,
            "remote": remote, "remote_port": remote_port, "protocol": protocol}


def give_mac(admin, name, mac):
    h = host(admin, name)
    h["interfaces"][0]["mac"] = mac
    put_host(admin, name, interfaces=h["interfaces"])


def test_suggestions_from_what_a_switch_sees(admin):
    give_mac(admin, "srv-pm-pve-02", "00:1a:2b:00:00:12")
    sw = {"host": "net-sw-core-01"}
    r = seen("snmp core",
             lk("ge-0/0/1", {"name": "srv-pm-pve-01"}, "eno1", **dict(sw)),                       # by name
             lk("ge-0/0/2", {"mac": "00:1a:2b:00:00:12"}, "eno1", **dict(sw)),                    # by an interface's MAC
             lk("ge-0/0/3", {"ip": "10.10.3.253", "name": "OPNsense.localdomain"}, "igb1", **dict(sw)),  # by address
             lk("ge-0/0/9", {"name": "printer-hall", "mac": "de:ad:be:ef:00:01", "platform": "HP"}, "1", **dict(sw)))
    assert r == {"links": 4, "matched_ends": 7}
    s = admin.get("/api/cables/suggestions").get_json()
    pairs = {(x["a_port"], x["b_host"], x["b_port"]) for x in s["suggested"]}
    assert pairs == {("ge-0/0/1", "srv-pm-pve-01", "eno1"), ("ge-0/0/2", "srv-pm-pve-02", "eno1"), ("ge-0/0/3", "net-fw-edge-01", "igb1")}
    assert [u["name"] for u in s["unknown"]] == ["printer-hall"] and s["unknown"][0]["platform"] == "HP"
    # the other end reports the same cable: still one suggestion
    seen("snmp pve", lk("eno1", {"name": "net-sw-core-01.mgmt.lab.example.net"}, "ge-0/0/1", host="srv-pm-pve-01"))
    assert len(admin.get("/api/cables/suggestions").get_json()["suggested"]) == 3

    # accept two, ignore one
    keys = {x["b_host"]: x["key"] for x in s["suggested"]}
    r = admin.post("/api/cables/suggestions/accept", json={"keys": [keys["srv-pm-pve-01"], keys["srv-pm-pve-02"]]})
    assert r.status_code == 200 and len(r.get_json()["made"]) == 2, r.get_json()
    c = next(c for c in host(admin, "srv-pm-pve-02")["cables"] if c["peer"] == "net-sw-core-01")
    assert (c["port"], c["peer_port"], c["notes"]) == ("eno1", "ge-0/0/2", "seen by snmp core (lldp)")
    assert admin.post("/api/cables/suggestions/ignore", json={"keys": [keys["net-fw-edge-01"]]}).get_json() == {"ignored": 1}
    s = admin.get("/api/cables/suggestions").get_json()
    assert (s["suggested"], s["documented"], s["ignored"]) == ([], 2, 1)
    hist = admin.get("/api/history?object_name=srv-pm-pve-02").get_json()
    assert "cable net-sw-core-01 ge-0/0/2 – srv-pm-pve-02 eno1" in hist[0]["summary"]


def test_a_conflict_and_replacing_it(admin):
    admin.post("/api/topology/cables", json={"a": {"host": "net-sw-core-01", "port": "ge-0/0/5"}, "b": {"host": "srv-vm-dns-01", "port": "eth9"}})
    seen("unifi u", lk("ge-0/0/5", {"name": "srv-pm-pve-01"}, "eno2", host="net-sw-core-01"))
    s = admin.get("/api/cables/suggestions").get_json()
    assert s["suggested"] == [] and len(s["conflicts"]) == 1
    x = s["conflicts"][0]
    assert x["now"]["a"] == "srv-vm-dns-01 eth9"
    r = admin.post("/api/cables/suggestions/accept", json={"keys": [x["key"]]})
    assert r.status_code == 422 and "already has a cable" in str(r.get_json())
    r = admin.post("/api/cables/suggestions/accept", json={"keys": [x["key"]], "replace": True})
    assert r.status_code == 200, r.get_json()
    sw = host(admin, "net-sw-core-01")
    assert [(c["peer"], c["peer_port"]) for c in sw["cables"] if c["port"] == "ge-0/0/5"] == [("srv-pm-pve-01", "eno2")]
    assert not any(c["port"] == "eth9" for c in host(admin, "srv-vm-dns-01")["cables"])


def test_rights_and_ports_by_their_own_names(admin, make_user):
    # the switch calls it GE-0/0/7; the inventory has ge-0/0/7: the cable uses the inventory's name
    sw = host(admin, "net-sw-core-01")
    put_host(admin, "net-sw-core-01", interfaces=[*sw["interfaces"], {"label": "ge-0/0/7", "kind": "ethernet"}])
    seen("snmp core", lk("GE-0/0/7", {"name": "srv-pm-pve-01"}, "eno3", host="net-sw-core-01"))
    v = make_user("v", role="viewer")
    assert len(v.get("/api/cables/suggestions").get_json()["suggested"]) == 1
    key = v.get("/api/cables/suggestions").get_json()["suggested"][0]["key"]
    assert v.post("/api/cables/suggestions/accept", json={"keys": [key]}).status_code == 403
    assert v.post("/api/cables/suggestions/ignore", json={"keys": [key]}).status_code == 403
    assert admin.post("/api/cables/suggestions/accept", json={"keys": [key]}).status_code == 200
    labels = [i["label"] for i in host(admin, "net-sw-core-01")["interfaces"]]
    assert "ge-0/0/7" in labels and "GE-0/0/7" not in labels


def test_unifi_reads_ports(admin):
    fake = FakeUniFiOS()
    core = fake.devices["default"][0]
    core["port_table"] = [{"port_idx": 1, "name": "Port 1"}, {"port_idx": 2, "name": "uplink fw"}, {"port_idx": 5, "name": "Port 5"}]
    core["uplink"] = {"uplink_mac": "aa:aa:aa:aa:aa:fe", "uplink_remote_port": 3, "port_idx": 2, "type": "wire"}
    core["lldp_table"] = [{"local_port_idx": 1, "chassis_id": "00:1a:2b:00:00:11", "port_id": "eno1", "system_name": "pve1", "is_wired": True}]
    fake.clients["default"].append({"mac": "00:1A:2B:00:00:53", "ip": "10.10.100.53", "hostname": "dns", "is_wired": True,
                                    "sw_mac": "aa:aa:aa:aa:aa:01", "sw_port": 5})
    # the inventory knows the switch by its address, the hypervisor and the DNS VM by their MACs
    give_mac(admin, "srv-pm-pve-01", "00:1a:2b:00:00:11")
    give_mac(admin, "srv-vm-dns-01", "00:1a:2b:00:00:53")
    srv = FakeServer(fake)
    try:
        make_instance(admin, "unifi", "unifi", {"auth": "password", "url": srv.url, "username": "viewer", "password": "pw"})
        job = run(admin, "unifi", "poll")
        assert job["status"] == "succeeded" and job["result"]["links"] == 3, job
    finally:
        srv.close()
    s = admin.get("/api/cables/suggestions").get_json()
    got = {(x["a_host"], x["a_port"], x["b_host"], x["b_port"]) for x in s["suggested"]}
    assert ("net-sw-core-01", "Port 1", "srv-pm-pve-01", "eno1") in got  # LLDP, the neighbour by its MAC
    dns_s = next(x for x in s["suggested"] if x["b_host"] == "srv-vm-dns-01")
    assert (dns_s["a_port"], dns_s["b_port"], dns_s["b_mac"]) == ("Port 5", "", "00:1a:2b:00:00:53")  # a wired client: no port name
    r = admin.post("/api/cables/suggestions/accept", json={"keys": [dns_s["key"]]})
    assert r.status_code == 200, r.get_json()
    d = host(admin, "srv-vm-dns-01")
    assert d["interfaces"][0]["label"] == "eth0" and d["interfaces"][0]["mac"] == "00:1a:2b:00:00:53"  # its interface got the name
    assert any(c["port"] == "eth0" and c["peer_port"] == "Port 5" for c in d["cables"])
    # the uplink's far end isn't in the inventory: an unknown neighbour
    assert any(u["mac"] == "aa:aa:aa:aa:aa:fe" and u["port"] == "uplink fw" for u in s["unknown"])


# ------------------------------------------------------------------------- SNMP
def _mib():
    from labdex import snmp as s

    def txt(v):
        return (s.OCTETS, v.encode())

    def num(n, tag=s.INTEGER):
        return (tag, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big"))

    rem, loc, man, cdp = "1.0.8802.1.1.2.1.4.1.1", "1.0.8802.1.1.2.1.3.7.1", "1.0.8802.1.1.2.1.4.2.1", "1.3.6.1.4.1.9.9.23.1.2.1.1"
    mib = {"1.3.6.1.2.1.1.5.0": txt("core"), "1.3.6.1.2.1.1.3.0": num(8640000 * 12, s.TICKS)}
    for i, n in ((1, "ge-0/0/1"), (2, "ge-0/0/2"), (3, "ge-0/0/3")):
        mib[f"1.3.6.1.2.1.31.1.1.1.1.{i}"] = txt(n)
        mib[f"{loc}.2.{i}"] = num(5)
        mib[f"{loc}.3.{i}"] = txt(n)
    # port 1: a neighbour known by its chassis MAC; port 2: by its DNS name, with a management address
    mib.update({f"{rem}.4.0.1.1": num(4), f"{rem}.5.0.1.1": (s.OCTETS, bytes.fromhex("001a2b000011")), f"{rem}.6.0.1.1": num(5),
                f"{rem}.7.0.1.1": txt("eno1"), f"{rem}.9.0.1.1": txt("pve1.local"),
                f"{rem}.4.0.2.1": num(7), f"{rem}.5.0.2.1": txt("x"), f"{rem}.6.0.2.1": num(3),
                f"{rem}.7.0.2.1": (s.OCTETS, bytes.fromhex("001a2b000099")), f"{rem}.8.0.2.1": txt("eno1"),
                f"{rem}.9.0.2.1": txt("srv-pm-pve-02.mgmt.lab.example.net"),
                f"{man}.3.0.2.1.1.4.10.10.3.12": num(2)})
    # CDP on ifIndex 3: the firewall, by its address
    mib.update({f"{cdp}.3.3.1": num(1), f"{cdp}.4.3.1": (s.OCTETS, bytes([10, 10, 3, 253])), f"{cdp}.6.3.1": txt("OPNsense.localdomain"),
                f"{cdp}.7.3.1": txt("igb1"), f"{cdp}.8.3.1": txt("FreeBSD")})
    return mib


def test_snmp_parses_lldp_and_cdp():
    from labdex import lldp
    from .fakes import FakeSnmpAgent
    from labdex import snmp
    agent = FakeSnmpAgent(_mib())
    try:
        with snmp.Client("127.0.0.1", agent.port, community="public") as c:
            names = lldp.if_names(c.walk(lldp.IF_NAME))
            got = lldp.lldp(c.walk(lldp.LLDP_REM), c.walk(lldp.LLDP_LOC_PORT), c.walk(lldp.LLDP_REM_MAN), names) + lldp.cdp(c.walk(lldp.CDP_CACHE), names)
        with snmp.Client("127.0.0.1", agent.port, community="wrong", timeout=0.3, retries=0) as c:
            try:
                c.get(lldp.SYS_NAME)
                raise AssertionError("a wrong community must not answer")
            except snmp.SnmpError as exc:
                assert "community" in str(exc)
    finally:
        agent.close()
    assert [(n["port"], n["protocol"], n["name"], n["remote_port"], n["mac"], n["ip"]) for n in got] == [
        ("ge-0/0/1", "lldp", "pve1", "eno1", "00:1a:2b:00:00:11", ""),
        ("ge-0/0/2", "lldp", "srv-pm-pve-02", "eno1", "", "10.10.3.12"),
        ("ge-0/0/3", "cdp", "OPNsense", "igb1", "", "10.10.3.253"),
    ]


def test_snmp_plugin_suggests_cables(admin):
    from .fakes import FakeSnmpAgent
    give_mac(admin, "srv-pm-pve-01", "00:1a:2b:00:00:11")
    agent = FakeSnmpAgent(_mib(), community="labro")
    try:
        r = admin.post("/api/integrations", json={"plugin_id": "snmp", "name": "snmp"})
        assert r.status_code == 201 and admin.get("/api/integrations/snmp").get_json()["selectors"] == ["tag:snmp"]
        assert admin.put("/api/integrations/snmp", json={"enabled": True, "settings": {"version": "v2c", "community": "labro", "port": agent.port,
                                                                                      "timeout": 1}}).status_code == 200
        job = run(admin, "snmp", "poll")
        assert job["status"] == "failed" and "tag your switches snmp" in job["log"], job
        put_host(admin, "net-sw-core-01", tags=["snmp"])
        assert admin.put("/api/hosts/net-sw-core-01/modules/snmp", json={"settings": {"address": "127.0.0.1"}}).status_code == 200
        assert "core" in run(admin, "snmp", "test")["result"]["message"]
        job = run(admin, "snmp", "poll")
        assert job["status"] == "succeeded" and job["result"]["links"] == 3, job
    finally:
        agent.close()
    s = admin.get("/api/cables/suggestions").get_json()
    got = {(x["a_port"], x["b_host"], x["b_port"]) for x in s["suggested"]}
    assert got == {("ge-0/0/1", "srv-pm-pve-01", "eno1"), ("ge-0/0/2", "srv-pm-pve-02", "eno1"), ("ge-0/0/3", "net-fw-edge-01", "igb1")}
    st = admin.get("/api/hosts/net-sw-core-01/widgets").get_json()
    w = next(x for x in st if x.get("plugin") == "snmp" or "Neighbours" in str(x))
    assert "3 neighbour" in str(w)


def test_snmpv3_against_net_snmp(tmp_path):
    """The v3 crypto (keys, HMAC, AES) against a real agent, when net-snmp's snmpd is installed."""
    import shutil
    import socket
    import subprocess
    import time
    import pytest
    from labdex import snmp
    if not shutil.which("snmpd") and not __import__("os").path.exists("/usr/sbin/snmpd"):
        pytest.skip("snmpd (net-snmp) isn't installed")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    conf = tmp_path / "snmpd.conf"
    conf.write_text(f"agentAddress udp:127.0.0.1:{port}\nrocommunity public 127.0.0.1\n"
                    'createUser labsha SHA "auth passphrase 1" AES "priv passphrase 1"\n'
                    'createUser lab256 SHA-256 "auth passphrase 2" AES "priv passphrase 2"\n'
                    'createUser labauth SHA "auth passphrase 3"\n'
                    "rouser labsha priv\nrouser lab256 priv\nrouser labauth auth\n")
    p = subprocess.Popen([shutil.which("snmpd") or "/usr/sbin/snmpd", "-f", "-Lo", "-C", "-c", str(conf), f"--persistentDir={tmp_path / 'p'}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def ask(**kw):
            with snmp.Client("127.0.0.1", port, timeout=1, retries=2, **kw) as c:
                return c.get("1.3.6.1.2.1.1.5.0").get("1.3.6.1.2.1.1.5.0"), len(c.walk("1.3.6.1.2.1.2.2"))
        for _ in range(30):
            try:
                ask(community="public")
                break
            except snmp.SnmpError:
                time.sleep(0.2)
        name, rows = ask(community="public")
        assert name and rows > 0
        assert ask(v3=snmp.V3Auth("labsha", "sha", "auth passphrase 1", "aes", "priv passphrase 1")) == (name, rows)
        assert ask(v3=snmp.V3Auth("lab256", "sha256", "auth passphrase 2", "aes", "priv passphrase 2")) == (name, rows)
        assert ask(v3=snmp.V3Auth("labauth", "sha", "auth passphrase 3")) == (name, rows)
        with pytest.raises(snmp.SnmpError, match="wrong authentication"):
            ask(v3=snmp.V3Auth("labsha", "sha", "not the passphrase", "aes", "priv passphrase 1"))
        with pytest.raises(snmp.SnmpError, match="unknown user"):
            ask(v3=snmp.V3Auth("nobody", "sha", "auth passphrase 1", "aes", "priv passphrase 1"))
    finally:
        p.terminate()
        p.wait(5)
