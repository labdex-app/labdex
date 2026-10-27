import http.server
import socket
import textwrap
import threading
from pathlib import Path

import pytest

from labdex.models import Integration, db

from .conftest import client
from .fakes import FakeOPNsense, FakeTechnitium

SAMPLE_XML = """<?xml version="1.0"?><nmaprun><host><status state="up"/><address addr="10.10.100.53" addrtype="ipv4"/>
<address addr="52:54:00:AB:CD:EF" addrtype="mac" vendor="QEMU virtual NIC"/>
<hostnames><hostname name="unown.lab"/></hostnames><ports>
<port protocol="tcp" portid="53"><state state="open"/><service name="domain" product="dnsmasq" version="2.90"/></port>
<port protocol="tcp" portid="22"><state state="open"/><service name="ssh" product="OpenSSH" version="9.6" extrainfo="Ubuntu"/></port>
<port protocol="tcp" portid="8080"><state state="filtered"/></port></ports></host>
<host><status state="down"/><address addr="10.10.100.54" addrtype="ipv4"/></host></nmaprun>"""


def make_instance(c, plugin, name, settings, enable=True):
    r = c.post("/api/integrations", json={"plugin_id": plugin, "name": name})
    assert r.status_code == 201, r.get_json()
    r = c.put(f"/api/integrations/{name}", json={"settings": settings, "enabled": enable})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def run(c, name, action):
    r = c.post(f"/api/integrations/{name}/actions/{action}")
    assert r.status_code == 202, r.get_json()
    return r.get_json()


# ------------------------------------------------------------------ framework
def test_builtin_plugins_load(admin):
    ids = {p["id"]: p for p in admin.get("/api/plugins").get_json()["plugins"]}
    assert {"exports", "yaml-mirror", "opnsense", "technitium", "nmap", "prometheus-sd", "motd"} <= set(ids)
    assert all(not p["error"] for p in ids.values())
    # schedulable actions get a schedule setting automatically
    assert any(s["key"] == "schedule_sync_aliases" for s in ids["opnsense"]["settings"])


def write_plugin(loaded, name, manifest, code=""):
    d = Path(loaded.config["LABDEX_PLUGIN_DIR"]) / name
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(textwrap.dedent(manifest))
    if code:
        (d / "plugin.py").write_text(textwrap.dedent(code))
    return d


DEMO = """
id: demo
name: Demo
settings:
  - {key: greeting, type: string, required: true}
  - {key: password, type: secret, required: true}
actions:
  - {id: hello, mode: run}
  - {id: change, mode: plan}
  - {id: boom, mode: run}
"""
DEMO_CODE = """
from labdex.plugins.base import Change, Plugin as Base, PluginError
class Plugin(Base):
    def run_hello(self, params):
        self.log(self.settings["greeting"])
        return {"secret_length": len(self.settings["password"]), "hosts": len(self.ctx.inventory.hosts())}
    def plan_change(self, params):
        return [Change("update", "thing", "x", "would change x")]
    def apply_change(self, changes, params):
        self.log("applied " + changes[0].name)
        return {"done": len(changes)}
    def run_boom(self, params):
        raise RuntimeError("plugin bug")
"""


def drop_in_app(tmp_path, manifest, code):
    """A fresh app whose plugin folder holds the given plugin."""
    from labdex import create_app, inventory
    plugdir = tmp_path / "plugins"
    d = plugdir / "demo"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(textwrap.dedent(manifest))
    if code:
        (d / "plugin.py").write_text(textwrap.dedent(code))
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'p.db'}", "TESTING": True, "SECRET_KEY": "s",
                      "LABDEX_PLUGIN_DIR": str(plugdir), "LABDEX_JOBS_SYNC": True, "LABDEX_SCHEDULER": False})
    ctx = app.app_context()
    ctx.push()
    from .conftest import EXAMPLE, ADMIN
    inventory.import_yaml(EXAMPLE.read_text())
    c = client(app)
    c.post("/api/auth/setup", json=ADMIN)
    return app, c, ctx


def test_drop_in_plugin_settings_jobs_plan_apply(tmp_path):
    app, c, ctx = drop_in_app(tmp_path, DEMO, DEMO_CODE)
    try:
        assert c.put("/api/integrations/x", json={}).status_code == 404
        c.post("/api/integrations", json={"plugin_id": "demo", "name": "demo1"})
        # enabling needs required settings
        r = c.put("/api/integrations/demo1", json={"enabled": True})
        assert r.status_code == 422
        r = c.put("/api/integrations/demo1", json={"settings": {"greeting": "hi", "password": "hunter2-secret"}, "enabled": True})
        assert r.status_code == 200
        d = r.get_json()
        assert d["settings"]["password"] == "" and d["secrets_set"] == ["password"]
        stored = db.session.scalar(db.select(Integration).where(Integration.name == "demo1")).settings
        assert "hunter2" not in str(stored) and "$enc" in str(stored["password"])
        # saving again with an empty secret keeps it
        c.put("/api/integrations/demo1", json={"settings": {"greeting": "hello", "password": ""}})

        job = run(c, "demo1", "hello")
        assert job["status"] == "succeeded" and job["result"] == {"secret_length": 14, "hosts": 15}
        assert "hello" in c.get(f"/api/jobs/{job['id']}").get_json()["log"]

        bug = run(c, "demo1", "boom")
        assert bug["status"] == "failed" and "plugin bug" in bug["result"]["error"]
        assert "Traceback" in c.get(f"/api/jobs/{bug['id']}").get_json()["log"]

        plan = run(c, "demo1", "change")
        assert plan["status"] == "planned" and plan["plan"][0]["summary"] == "would change x"
        applied = c.post(f"/api/jobs/{plan['id']}/apply").get_json()
        assert applied["status"] == "applied" and applied["result"] == {"done": 1}
        assert c.post(f"/api/jobs/{plan['id']}/apply").status_code == 409

        # a plan goes stale when the inventory changes in between
        plan2 = run(c, "demo1", "change")
        c.post("/api/aliases", json={"name": "SG_X", "type": "port", "members": ["1"]})
        r = c.post(f"/api/jobs/{plan2['id']}/apply")
        # approving it plans again; the same changes keep the approval and apply
        d = r.get_json()
        assert r.status_code == 202 and d["status"] == "stale" and d["outcome"] == "replanned"
        again = c.get(f"/api/jobs/{d['replanned_as']}").get_json()
        assert again["status"] == "applied" and again["replaces"] == plan2["id"] and again["approvals"][0]["by"] == "root"
        # a newer plan supersedes an older one
        p3, p4 = run(c, "demo1", "change"), run(c, "demo1", "change")
        assert c.get(f"/api/jobs/{p3['id']}").get_json()["status"] == "stale"
        assert c.post(f"/api/jobs/{p4['id']}/discard").get_json()["status"] == "discarded"
        assert any(e["object_type"] == "integration" and e["action"] == "apply" for e in c.get("/api/history").get_json())
    finally:
        ctx.pop()


def test_broken_plugin_doesnt_break_labdex(tmp_path):
    # unsigned, so isolated: labdex reads the file instead of running it, and a broken one
    # fails on its first call, in its own process
    app, c, ctx = drop_in_app(tmp_path, "id: demo\nactions: [{id: a, mode: run}]\n",
                              "raise ImportError('nope')\nclass Plugin:\n    def run_a(self, params): pass\n")
    try:
        p = next(x for x in c.get("/api/plugins").get_json()["plugins"] if x["id"] == "demo")
        assert p["isolated"] and not p["error"]
        make_instance(c, "demo", "demo", {})
        job = run(c, "demo", "a")
        assert job["status"] == "failed" and job["result"]["error"] == "ImportError: nope"
        assert c.get("/api/hosts").status_code == 200
    finally:
        ctx.pop()
    # not even a class Plugin: refused when loading
    app, c, ctx = drop_in_app(tmp_path / "2", "id: demo\nactions: [{id: a, mode: run}]\n", "x = (\n")
    try:
        p = next(x for x in c.get("/api/plugins").get_json()["plugins"] if x["id"] == "demo")
        assert p["error"].startswith("plugin.py: SyntaxError")
    finally:
        ctx.pop()


def test_integration_permissions(make_user, admin):
    make_instance(admin, "exports", "files", {})
    viewer = make_user("v", role="viewer")
    assert viewer.get("/api/integrations/files").status_code == 200
    assert viewer.get("/api/integrations/files/export/opnsense_json").status_code == 403
    assert viewer.post("/api/integrations/files/actions/x").status_code == 403
    op = make_user("o", role="operator", scopes=["name:files"])
    r = op.get("/api/integrations/files/export/opnsense_json?download=1")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and "N4_ZONE_DC_HT_MGMT" in r.get_data(as_text=True)
    assert op.put("/api/integrations/files", json={"enabled": False}).status_code == 403
    scoped = make_user("s", role="operator", scopes=["name:other"])
    assert scoped.get("/api/integrations/files").status_code == 404


# ------------------------------------------------------------------- modules
def test_yaml_mirror_hook(admin, tmp_path):
    path = tmp_path / "mirror" / "inventory.yaml"
    make_instance(admin, "yaml-mirror", "mirror", {"path": str(path)})
    h = admin.get("/api/hosts/sonos-living").get_json()
    admin.put("/api/hosts/sonos-living", json={**h, "function": "Kitchen speaker"})
    assert "Kitchen speaker" in path.read_text()
    jobs = admin.get("/api/jobs?integration=mirror").get_json()
    assert jobs[0]["trigger"] == "hook" and jobs[0]["status"] == "succeeded"


def test_prometheus_sd(admin):
    make_instance(admin, "prometheus-sd", "prom", {"exporter_port": 9100})
    nodes = admin.get("/api/integrations/prom/endpoints/nodes").get_json()
    fw = next(n for n in nodes if n["labels"]["host"] == "net-fw-edge-01")
    assert fw["targets"] == ["10.10.3.253:9100"] and fw["labels"]["fun"] == "arcanine" and fw["labels"]["zone"] == "DC_HT"
    assert all(n["labels"]["status"] == "active" for n in nodes)
    svcs = admin.get("/api/integrations/prom/endpoints/services").get_json()
    assert {"targets": ["https://grafana.lab.example.net"]}.items() <= next(s for s in svcs if s["labels"]["service"] == "grafana").items()


# ----------------------------------------------------------------- OPNsense
@pytest.fixture
def opn():
    f = FakeOPNsense()
    yield f
    f.srv.close()


def test_opnsense_sync(admin, opn):
    opn.add("HG_ZONE_CA_ST_ADMIN", "1.2.3.4", "hand-made")  # same name, not ours
    opn.add("H_OLD_THING", "10.0.0.1", "old [labdex]")     # ours, no longer wanted
    opn.add("SOMETHING_ELSE", "10.0.0.2", "hand-made")      # not ours, leave alone
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": "k", "api_secret": "s", "verify_tls": False})
    assert run(admin, "fw", "test")["result"]["message"].startswith("Connected; 3 aliases")

    plan = run(admin, "fw", "sync_aliases")
    ops = {(c["op"], c["name"]) for c in plan["plan"]}
    assert ("delete", "H_OLD_THING") in ops
    assert not any(n == "HG_ZONE_CA_ST_ADMIN" for _, n in ops)  # unmanaged: skipped
    assert not any(n == "SOMETHING_ELSE" for _, n in ops)
    assert admin.post(f"/api/jobs/{plan['id']}/apply").get_json()["status"] == "applied"
    assert opn.reconfigured == 1 and opn.by_name("H_OLD_THING") is None
    assert opn.by_name("NG_ZONE_DC")["content"] == "NG_ZONE_DC_HT\nNG_ZONE_DC_TC"
    assert opn.by_name("SOMETHING_ELSE") is not None

    assert run(admin, "fw", "sync_aliases")["status"] == "succeeded"  # nothing left to do

    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    admin.put("/api/hosts/srv-vm-dns-01", json={**h, "interfaces": [{"network": "srv", "ip4": "10.10.100.54"}]})
    # the edit triggered a hook plan
    jobs = admin.get("/api/jobs?integration=fw&status=planned").get_json()
    assert jobs and jobs[0]["trigger"] == "hook", [(j["action"], j["status"], j["trigger"], j["result"]) for j in admin.get("/api/jobs?integration=fw").get_json()]
    detail = admin.get(f"/api/jobs/{jobs[0]['id']}").get_json()
    assert {c["name"] for c in detail["plan"]} == {"H4_ZONE_DC_TC_SRV_VM_DNS_01", "H6_ZONE_DC_TC_SRV_VM_DNS_01", "H_ZONE_DC_TC_SRV_VM_DNS_01"}


def test_opnsense_bad_credentials_and_arp(admin, opn):
    make_instance(admin, "opnsense", "fw", {"url": opn.url, "api_key": "k", "api_secret": "wrong", "verify_tls": False})
    job = run(admin, "fw", "test")
    assert job["status"] == "failed" and "access denied" in job["result"]["error"]
    admin.put("/api/integrations/fw", json={"settings": {"api_secret": "s"}})
    assert run(admin, "fw", "import_arp")["result"] == {"entries": 2}  # ARP + one NDP neighbour (link-local skipped)
    unknown = admin.get("/api/discovery/unknown-hosts").get_json()["items"]
    assert unknown[0]["ip"] == "10.10.10.77" and unknown[0]["mac"] == "aa:bb:cc:00:00:77" and unknown[0]["network"] == "main"


# --------------------------------------------------------------- Technitium
def test_technitium_sync(admin):
    t = FakeTechnitium("lab.example.net")
    try:
        make_instance(admin, "technitium", "dns", {"url": t.url, "token": "t0ken", "verify_tls": False})
        plan = run(admin, "dns", "sync_records")
        assert plan["status"] == "planned"
        assert not any("manual." in c["name"] for c in plan["plan"])  # unmanaged record untouched
        admin.post(f"/api/jobs/{plan['id']}/apply")
        names = {(r["name"], r["type"]) for r in t.records}
        assert ("arcanine.lab.example.net", "CNAME") in names and ("_grafana._tcp.lab.example.net", "SRV") in names
        assert run(admin, "dns", "sync_records")["status"] == "succeeded"
        admin.delete("/api/hosts/srv-vm-mon-01")
        hook = admin.get("/api/jobs?integration=dns&status=planned").get_json()[0]
        detail = admin.get(f"/api/jobs/{hook['id']}").get_json()
        assert detail["plan"] and all(c["op"] == "delete" for c in detail["plan"])
        admin.post(f"/api/jobs/{hook['id']}/apply")
        assert not any("alakazam" in r["name"] for r in t.records)
        assert any(r["name"] == "manual.lab.example.net" for r in t.records)
    finally:
        t.srv.close()


# --------------------------------------------------------------------- nmap
def test_nmap_parse():
    from labdex.plugins import Registry
    from labdex.plugins import BUILTIN_DIR
    spec = Registry(None).get("nmap")
    hosts = spec.module.parse(SAMPLE_XML)
    assert hosts == [{"ip": "10.10.100.53", "hostname": "unown.lab", "services": [
        {"port": 53, "proto": "tcp", "service": "domain", "product": "dnsmasq", "version": "2.90"},
        {"port": 22, "proto": "tcp", "service": "ssh", "product": "OpenSSH", "version": "9.6 Ubuntu"}],
        "mac": "52:54:00:AB:CD:EF", "vendor": "QEMU virtual NIC"}]
    assert BUILTIN_DIR.is_dir()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(not __import__("shutil").which("nmap"), reason="nmap not installed")
def test_nmap_real_scan_and_classification(admin):
    port = _free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        admin.post("/api/networks", json={"name": "lo", "zone": "DC_TC", "prefix4": "127.0.0.0/30", "in_dns": False})
        admin.post("/api/hosts", json={"name": "srv-vm-lo-01", "zone": "DC_TC",
                                       "interfaces": [{"network": "lo", "ip4": "127.0.0.1"}],
                                       "services": [{"name": "ghost", "port": _free_port()}]})
        make_instance(admin, "nmap", "scan", {"networks": ["lo"], "ports": str(port), "service_detection": False, "timing": "T4"})
        # targets outside the inventory are refused
        admin.put("/api/integrations/scan", json={"settings": {"extra_targets": ["8.8.8.8"]}})
        assert "isn't inside an inventory network" in run(admin, "scan", "scan")["result"]["error"]
        admin.put("/api/integrations/scan", json={"settings": {"extra_targets": []}})
        job = run(admin, "scan", "scan")
        assert job["status"] == "succeeded", admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
        svc = next(s for s in admin.get("/api/discovery/services").get_json()["items"] if s["port"] == port)
        assert svc["ip"] == "127.0.0.1" and svc["status"] == "undocumented" and svc["host"] == "srv-vm-lo-01"
        missing = admin.get("/api/discovery/missing").get_json()["items"]
        assert any(m["service"] == "ghost" for m in missing)
        r = admin.post(f"/api/discovery/services/{svc['id']}/adopt", json={"name": "web"})
        assert r.status_code == 201
        assert not any(s["port"] == port for s in admin.get("/api/discovery/services").get_json()["items"])  # documented: hidden
        svc = next(s for s in admin.get("/api/discovery/services?status=all").get_json()["items"] if s["port"] == port)
        assert svc["status"] == "documented" and svc["declared_as"] == "web"
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- Ansible
@pytest.mark.skipif(not __import__("shutil").which("ansible-playbook"), reason="ansible not installed")
def test_motd_plan_apply_with_ansible(admin, tmp_path):
    motd, prompt = tmp_path / "motd", tmp_path / "prompt.sh"
    make_instance(admin, "motd", "banner", {"connection": "local", "become": False, "limit": "srv-vm-dns-01",
                                             "motd_path": str(motd), "prompt_path": str(prompt), "host_key_checking": False})
    plan = run(admin, "banner", "deploy")
    log = admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert plan["status"] == "planned", log
    assert not motd.exists()  # check mode changes nothing
    done = admin.post(f"/api/jobs/{plan['id']}/apply").get_json()
    assert done["status"] == "applied", admin.get(f"/api/jobs/{plan['id']}").get_json()["log"]
    assert "unown  ·  srv-vm-dns-01" in motd.read_text()
    assert "[srv-vm-dns-01 · DC_TC]" in prompt.read_text()
    assert run(admin, "banner", "deploy")["status"] == "succeeded"  # idempotent: nothing to change
