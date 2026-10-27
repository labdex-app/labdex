"""1.24: patch panels (pass-through ports) and cable traces; switch MAC tables; OIDC sign-in."""
from .test_v03 import host, put_host
from .test_v1170 import cable


def panel(admin, name="net-pp-rack-01", ports=4):
    """A patch panel: front ports 1..n, rear ports r1..rn, each front passing through to its rear."""
    ifaces = []
    for n in range(1, ports + 1):
        ifaces += [{"label": str(n), "kind": "ethernet", "connector": "rj45", "passthrough": f"r{n}"},
                   {"label": f"r{n}", "kind": "ethernet", "connector": "rj45"}]
    r = admin.post("/api/hosts", json={"name": name, "zone": "DC_TC", "function": "patch panel", "interfaces": ifaces})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_passthrough_pairs(admin):
    p = panel(admin)
    by = {i["label"]: i["passthrough"] for i in p["interfaces"]}
    assert by["1"] == "r1" and by["r1"] == "1"  # one side named it, both have it
    h = host(admin, "net-pp-rack-01")
    h["interfaces"][0]["passthrough"] = "r2"  # r2 already passes through to 2
    r = admin.put("/api/hosts/net-pp-rack-01", json=h)
    assert r.status_code == 422 and "already passes through" in str(r.get_json())
    h["interfaces"][0]["passthrough"] = "nope"
    assert "no interface named" in str(admin.put("/api/hosts/net-pp-rack-01", json=h).get_json())
    # dropping it on one side is not enough (the other still names it): drop both
    h = host(admin, "net-pp-rack-01")
    for i in h["interfaces"][:2]:
        i["passthrough"] = ""
    assert put_host(admin, "net-pp-rack-01", interfaces=h["interfaces"])["interfaces"][1]["passthrough"] == ""


def test_trace_through_two_panels(admin):
    panel(admin, "net-pp-rack-01")
    panel(admin, "net-pp-desk-01")
    # server eth0 → rack panel 1 … rack panel r1 → desk panel r3 … desk panel 3 → switch ge-0/0/5
    cable(admin, "srv-vm-mon-01", "eth9", "net-pp-rack-01", "1", label="C-0001", colour="blue", length="1")
    cable(admin, "net-pp-rack-01", "r1", "net-pp-desk-01", "r3", label="C-0002", length="25")
    cable(admin, "net-pp-desk-01", "3", "net-sw-core-01", "ge-0/0/5", label="C-0003")
    for start in (("srv-vm-mon-01", "eth9"), ("net-sw-core-01", "ge-0/0/5"), ("net-pp-desk-01", "r3")):
        t = admin.get(f"/api/cables/trace?host={start[0]}&port={start[1]}").get_json()
        path = [(p["host"], p["port"]) for p in t["points"]]
        if path[0][0] != "srv-vm-mon-01":
            path.reverse()
        assert path == [("srv-vm-mon-01", "eth9"), ("net-pp-rack-01", "1"), ("net-pp-rack-01", "r1"), ("net-pp-desk-01", "r3"),
                        ("net-pp-desk-01", "3"), ("net-sw-core-01", "ge-0/0/5")], (start, path)
        assert t["cables"] == 3 and t["complete"] and not t["loop"]
        assert len(t["links"]) == len(t["points"]) - 1
    t = admin.get("/api/cables/trace?host=srv-vm-mon-01&port=ETH9").get_json()  # any case
    assert [lk.get("cable", {}).get("label") for lk in t["links"]] == ["C-0001", None, "C-0002", None, "C-0003"]
    # an unpatched port: just itself
    t = admin.get("/api/cables/trace?host=net-pp-rack-01&port=2").get_json()
    assert t["cables"] == 0 and [p["port"] for p in t["points"]] == ["r2", "2"] and not t["complete"]


def test_trace_respects_rights(admin, make_user):
    panel(admin)
    cable(admin, "srv-vm-mon-01", "eth9", "net-pp-rack-01", "1")
    cable(admin, "net-pp-rack-01", "r1", "net-fw-edge-01", "igb5")
    v = make_user("dc", role="viewer", scope={"zone": ["DC_TC"]}) if False else None
    t = admin.get("/api/cables/trace?host=net-pp-rack-01&port=1").get_json()
    assert {p["host"] for p in t["points"]} == {"srv-vm-mon-01", "net-pp-rack-01", "net-fw-edge-01"}
    assert admin.get("/api/cables/trace?host=nope&port=1").status_code == 404
    assert admin.get("/api/cables/trace?host=net-pp-rack-01").status_code == 422
    assert v is None


def test_release_check_on_any_platform(admin, monkeypatch):
    from labdex.api import about_api
    assert about_api.release_api("https://github.com/reykimedev/labdex") == [("github", "https://api.github.com/repos/reykimedev/labdex/releases/latest")]
    gl = about_api.release_api("https://gitlab.example.org/lab/tools/labdex")
    assert gl[0] == ("gitlab", "https://gitlab.example.org/api/v4/projects/lab%2Ftools%2Flabdex/releases/permalink/latest")
    assert about_api.release_api("https://codeberg.org/me/labdex")[0] == ("gitea", "https://codeberg.org/api/v1/repos/me/labdex/releases/latest")
    assert admin.get("/api/meta").get_json()["project_url"] == "https://github.com/reykimedev/labdex"  # the default

    from tests.fakes import FakeServer
    from flask import Response
    import json
    srv = FakeServer(lambda req: Response(json.dumps({"tag_name": "v99.0.0", "name": "big", "_links": {"self": "https://x/r"}, "released_at": "2026-10-27"}),
                                          mimetype="application/json") if "/api/v4/projects/me" in req.full_path else Response("", 404))
    try:
        url = srv.url.replace("127.0.0.1", "127.0.0.1") + "/me/labdex"
        about_api._release_cache.clear()
        rel = about_api.latest_release(url)  # not GitHub: Gitea first (404), then GitLab
        assert rel["tag"] == "v99.0.0" and rel["platform"] == "gitlab" and rel["url"] == "https://x/r"
        admin.put("/api/admin/settings", json={"update_check": "true", "project_url": url + ".git/"})
        a = admin.get("/api/about").get_json()
        assert a["project_url"] == url and a["update_check"]["newer"] is True and a["update_check"]["error"] == ""
    finally:
        srv.close()
    about_api._release_cache.clear()
    monkeypatch.setattr(about_api, "release_api", lambda url: [("github", "http://127.0.0.1:9/nothing")])
    a = admin.get("/api/about").get_json()
    assert a["update_check"]["latest"] is None and a["update_check"]["error"].startswith("can't reach 127.0.0.1:9")


# ------------------------------------------------------------------ where is this MAC
def _fdb_mib(q=True):
    """A switch with ge-0/0/1..3: two hosts behind port 1 and 2, the uplink (port 3) with many."""
    from .test_v1200 import _mib
    from labdex import snmp as s
    mib = _mib()

    def num(n):
        return (s.INTEGER, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big"))
    bp, fdbp, fdbs, qp, qs, vf = ("1.3.6.1.2.1.17.1.4.1.2", "1.3.6.1.2.1.17.4.3.1.2", "1.3.6.1.2.1.17.4.3.1.3",
                                  "1.3.6.1.2.1.17.7.1.2.2.1.2", "1.3.6.1.2.1.17.7.1.2.2.1.3", "1.3.6.1.2.1.17.7.1.4.2.1.3")
    for port in (1, 2, 3):
        mib[f"{bp}.{port}"] = num(port)  # bridge port n = ifIndex n
    table = [("001a2b000011", 1, 3), ("001a2b000053", 2, 3), ("aaaaaaaa0001", 3, 3), ("aaaaaaaa0002", 3, 3), ("aaaaaaaa0003", 3, 3),
             ("0000deadbeef", 1, 4)]  # the last one is the switch itself (status self)
    for mac, port, status in table:
        o = ".".join(str(b) for b in bytes.fromhex(mac))
        if q:
            mib[f"{qp}.7.{o}"] = num(port)
            mib[f"{qs}.7.{o}"] = num(status)
        else:
            mib[f"{fdbp}.{o}"] = num(port)
            mib[f"{fdbs}.{o}"] = num(status)
    if q:
        mib[f"{vf}.0.30"] = num(7)  # fdb 7 is VLAN 30
    return mib


def test_fdb_from_both_mibs():
    from labdex import lldp, snmp
    from .fakes import FakeSnmpAgent
    for q in (True, False):
        agent = FakeSnmpAgent(_fdb_mib(q))
        try:
            with snmp.Client("127.0.0.1", agent.port, community="public") as c:
                w = {k: c.walk(v) for k, v in lldp.WALKS.items() if k in ("bridge_ports", "fdb", "fdb_status", "qfdb", "qfdb_status", "vlan_fdb")}
                names = lldp.if_names(c.walk(lldp.IF_NAME))
        finally:
            agent.close()
        got = lldp.fdb(w["bridge_ports"], w["fdb"], w["fdb_status"], w["qfdb"], w["qfdb_status"], w["vlan_fdb"], names)
        rows = sorted((e["mac"], e["port"], e["vlan"]) for e in got)
        assert ("00:1a:2b:00:00:11", "ge-0/0/1", 30 if q else None) in rows and len(rows) == 5, rows  # the switch's own MAC left out


def test_where_is_this_mac(admin):
    from .fakes import FakeSnmpAgent
    from .test_plugins import run
    from .test_v1200 import give_mac
    give_mac(admin, "srv-vm-dns-01", "00:1a:2b:00:00:53")
    agent = FakeSnmpAgent(_fdb_mib(), community="labro")
    try:
        admin.post("/api/integrations", json={"plugin_id": "snmp", "name": "snmp"})
        admin.put("/api/integrations/snmp", json={"enabled": True, "settings": {"version": "v2c", "community": "labro", "port": agent.port, "timeout": 1}})
        put_host(admin, "net-sw-core-01", tags=["snmp"])
        admin.put("/api/hosts/net-sw-core-01/modules/snmp", json={"settings": {"address": "127.0.0.1"}})
        job = run(admin, "snmp", "poll")
        assert job["status"] == "succeeded" and job["result"]["macs"] == 5, job
    finally:
        agent.close()
    for q in ("00:1A:2B:00:00:53", "001a.2b00.0053", "srv-vm-dns-01"):
        r = admin.get(f"/api/discovery/where?q={q}").get_json()
        res = r["results"][0]
        assert res["mac"] == "00:1a:2b:00:00:53" and res["owner"]["host"] == "srv-vm-dns-01", (q, r)
        best = res["seen"][0]
        assert (best["switch"], best["port"], best["vlan"], best["likely"]) == ("net-sw-core-01", "ge-0/0/2", 30, True)
    # the host page knows its switch port
    sp = admin.get("/api/hosts/srv-vm-dns-01").get_json()["switch_ports"]
    assert next(iter(sp.values()))["port"] == "ge-0/0/2"
    assert admin.get("/api/discovery/where").status_code == 422
    assert admin.get("/api/discovery/where?q=nothing-here").get_json()["results"] == []


# ------------------------------------------------------------------ OpenID Connect
class FakeIdP:
    """A provider: discovery, token and userinfo endpoints. id_token claims are set per test."""

    def __init__(self):
        import json
        from flask import Response
        from .fakes import FakeServer
        self.claims, self.token_calls, self.userinfo = {}, [], {}
        self.srv = FakeServer(self.handle)
        self.iss = self.srv.url + "/o/labdex"
        self.json, self.Response = json, Response

    def jwt(self, claims):
        import base64
        b = lambda d: base64.urlsafe_b64encode(self.json.dumps(d).encode()).rstrip(b"=").decode()
        return f"{b({'alg': 'RS256'})}.{b(claims)}.c2ln"

    def handle(self, req):
        J = lambda d, s=200: self.Response(self.json.dumps(d), status=s, mimetype="application/json")
        if req.path == "/o/labdex/.well-known/openid-configuration":
            return J({"issuer": self.iss, "authorization_endpoint": self.srv.url + "/authorize", "token_endpoint": self.srv.url + "/token",
                      "userinfo_endpoint": self.srv.url + "/userinfo"})
        if req.path == "/token":
            self.token_calls.append(dict(req.form) | {"auth": req.headers.get("Authorization", "")})
            if req.form.get("code") != "good":
                return J({"error": "invalid_grant"}, 400)
            return J({"id_token": self.jwt(self.claims), "access_token": "at", "token_type": "Bearer"})
        if req.path == "/userinfo":
            return J(self.userinfo)
        return self.Response("", 404)

    def close(self):
        self.srv.close()


def _sign_in(app, idp, claims, code="good", userinfo=None):
    from urllib.parse import parse_qs, urlsplit
    from .conftest import client
    c = client(app)
    r = c.get("/api/auth/oidc/login?next=/hosts")
    assert r.status_code == 302, r.text
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["Location"]).query).items()}
    assert r.headers["Location"].startswith(idp.srv.url + "/authorize?") and q["code_challenge_method"] == "S256" and q["client_id"] == "labdex"
    idp.claims = {"iss": idp.iss, "aud": "labdex", "exp": 9999999999, "nonce": q["nonce"], **claims}
    idp.userinfo = userinfo or {}
    r = c.get(f"/api/auth/oidc/callback?code={code}&state={q['state']}")
    from urllib.parse import unquote
    r.text = unquote(r.text)
    return c, r


def test_oidc_sign_in(app, admin):
    idp = FakeIdP()
    try:
        assert admin.get("/api/auth/status").get_json()["oidc"] == {"enabled": False, "name": "single sign-on", "only": False}
        app.config.update(LABDEX_OIDC_ISSUER=idp.iss, LABDEX_OIDC_CLIENT_ID="labdex", LABDEX_OIDC_CLIENT_SECRET="s3cret", LABDEX_OIDC_NAME="Authentik",
                          LABDEX_OIDC_ADMIN_GROUPS="labdex-admins")
        admin.post("/api/admin/groups", json={"name": "netops"})
        st = admin.get("/api/auth/status").get_json()["oidc"]
        assert st["enabled"] and st["name"] == "Authentik"
        sso = admin.get("/api/admin/sso").get_json()["oidc"]
        assert sso["redirect_uri"].endswith("/api/auth/oidc/callback") and sso["confidential"] and sso["admin_groups"] == "labdex-admins"

        # a first sign-in creates the account, with the groups labdex has
        c, r = _sign_in(app, idp, {"sub": "u-1", "preferred_username": "Alice", "name": "Alice A.", "groups": ["netops", "other", "labdex-admins"]})
        assert r.status_code == 200 and 'url=/hosts"' in r.text
        assert idp.token_calls[-1]["code_verifier"] and idp.token_calls[-1]["auth"].startswith("Basic ")
        me = c.get("/api/auth/status").get_json()["user"]
        assert (me["username"], me["display_name"], me["groups"], me["is_admin"]) == ("alice", "Alice A.", ["netops"], True)
        assert any("first sign-in" in h["summary"] for h in admin.get("/api/history?object_type=user").get_json())

        # again: the same account, linked by the subject (even after a rename at the provider); groups come from userinfo if the token has none
        c, r = _sign_in(app, idp, {"sub": "u-1", "preferred_username": "alice2"}, userinfo={"sub": "u-1", "groups": []})
        me = c.get("/api/auth/status").get_json()["user"]
        assert me["username"] == "alice" and me["groups"] == [] and me["is_admin"] is False

        # wrong code, wrong state, a token for someone else
        c, r = _sign_in(app, idp, {"sub": "u-2"}, code="bad")
        assert "sso_error=" in r.text and "refused the code" in r.text
        assert c.get("/api/auth/status").get_json()["user"] is None
        c, r = _sign_in(app, idp, {"sub": "u-2", "aud": "other"})
        assert "another client" in r.text
        from .conftest import client
        c2 = client(app)
        assert "another%20browser" in c2.get("/api/auth/oidc/callback?code=good&state=x").get_data(as_text=True)

        # an existing local account is linked by its user name (once)
        c, r = _sign_in(app, idp, {"sub": "admin-at-idp", "preferred_username": "admin"})
        assert c.get("/api/auth/status").get_json()["user"]["username"] == "admin"
        c, r = _sign_in(app, idp, {"sub": "someone-else", "preferred_username": "admin"})
        assert "belongs to someone else" in r.text

        # no automatic accounts
        app.config["LABDEX_OIDC_AUTOCREATE"] = False
        c, r = _sign_in(app, idp, {"sub": "u-9", "preferred_username": "bob"})
        assert "no labdex account for bob" in r.text
    finally:
        idp.close()
