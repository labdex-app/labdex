from labdex.models import AuditEvent, db

from .conftest import ADMIN, client


def test_setup_once_then_login(loaded):
    c = client(loaded)
    assert c.get("/api/auth/status").get_json()["setup_required"] is True
    assert c.get("/api/hosts").status_code == 401
    assert c.post("/api/auth/setup", json={"username": "root", "password": "short"}).status_code == 422
    assert c.post("/api/auth/setup", json=ADMIN).status_code == 201
    assert c.get("/api/hosts").status_code == 200
    assert client(loaded).post("/api/auth/setup", json={**ADMIN, "username": "evil"}).status_code == 409
    other = client(loaded)
    assert other.post("/api/auth/login", json={**ADMIN, "password": "wrong"}).status_code == 401
    assert other.post("/api/auth/login", json=ADMIN).status_code == 200
    assert other.get("/api/auth/me").get_json()["is_admin"]


def test_login_throttled(admin, loaded):
    c = client(loaded)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "root", "password": "nope"}).status_code == 401
    r = c.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 429


def test_csrf_header_required_for_cookie_writes(admin, loaded):
    admin.environ_base.pop("HTTP_X_REQUESTED_WITH")
    r = admin.post("/api/aliases", json={"name": "SG_X", "type": "port", "members": ["1"]})
    assert r.status_code == 403
    assert admin.get("/api/hosts").status_code == 200  # reads are fine


def test_tokens(admin, loaded):
    r = admin.post("/api/auth/tokens", json={"name": "ci"})
    token = r.get_json()["token"]
    ro = admin.post("/api/auth/tokens", json={"name": "prom", "read_only": True}).get_json()["token"]
    bare = loaded.test_client()  # no CSRF header, no cookie
    assert bare.post("/api/aliases", json={"name": "SG_X", "type": "port", "members": ["1"]},
                     headers={"Authorization": f"Bearer {token}"}).status_code == 201
    assert bare.post("/api/aliases", json={"name": "SG_Y", "type": "port", "members": ["1"]},
                     headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert bare.get("/api/hosts", headers={"Authorization": f"Bearer {ro}"}).status_code == 200
    assert bare.get("/api/hosts", headers={"Authorization": f"Bearer {token}x"}).status_code == 401
    tid = admin.get("/api/auth/tokens").get_json()[0]["id"]
    assert admin.delete(f"/api/auth/tokens/{tid}").status_code == 204
    assert bare.get("/api/hosts", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_password_reset_ends_sessions(make_user, admin):
    alice = make_user("alice", role="viewer")
    assert alice.get("/api/hosts").status_code == 200
    uid = next(u["id"] for u in admin.get("/api/admin/users").get_json() if u["username"] == "alice")
    assert admin.put(f"/api/admin/users/{uid}", json={"password": "another-password-1"}).status_code == 200
    assert alice.get("/api/hosts").status_code == 401


def test_admin_cant_lock_themselves_out(admin):
    me = admin.get("/api/auth/me").get_json()
    assert admin.put(f"/api/admin/users/{me['id']}", json={"is_admin": False}).status_code == 409
    assert admin.delete(f"/api/admin/users/{me['id']}").status_code == 409


def test_sso_header_only_from_trusted_proxy(admin, loaded):
    loaded.config.update(LABDEX_SSO_HEADER="Remote-User", LABDEX_SSO_GROUPS_HEADER="Remote-Groups",
                         LABDEX_TRUSTED_PROXIES=["10.0.0.0/24"], LABDEX_SSO_AUTOCREATE=True)
    admin.post("/api/admin/groups", json={"name": "lab-viewers"})
    admin.post("/api/admin/bindings", json={"group": "lab-viewers", "role": "viewer"})
    untrusted = client(loaded, REMOTE_ADDR="192.0.2.5")
    assert untrusted.get("/api/hosts", headers={"Remote-User": "root"}).status_code == 401
    proxy = client(loaded, REMOTE_ADDR="10.0.0.2")
    r = proxy.get("/api/hosts", headers={"Remote-User": "bob", "Remote-Groups": "lab-viewers"})
    assert r.status_code == 200 and len(r.get_json()) == 15
    me = proxy.get("/api/auth/me", headers={"Remote-User": "bob"}).get_json()
    assert me["via"] == "sso" and me["groups"] == ["lab-viewers"] and not me["is_admin"]


def test_logins_are_audited(admin):
    assert db.session.scalar(db.select(AuditEvent).where(AuditEvent.action == "create", AuditEvent.object_type == "user")) is not None
