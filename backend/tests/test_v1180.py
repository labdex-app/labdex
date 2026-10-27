"""1.18: one secret store. Global secrets are vault entries; plugins and Ansible point at vault
entries; pointing at one needs secret:apply on it; older secrets move in on the first unseal."""
from flask import current_app

from labdex import keyvault
from labdex.models import Secret, VaultEntry, db

from .fakes import FakeOPNsense
from .test_v03 import host, put_host
from .test_plugins import run


def entry(admin, path, fields, host=None):
    r = admin.post("/api/vault/entries", json={"path": path, "fields": fields, **({"host": host} if host else {})})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_plugins_use_vault_entries(vault):
    admin = vault
    entry(admin, "fw/opnsense", {"key": "k", "secret": "s"})
    opn = FakeOPNsense()
    try:
        admin.post("/api/integrations", json={"plugin_id": "opnsense", "name": "fw"})
        r = admin.put("/api/integrations/fw", json={"enabled": True, "settings": {
            "url": opn.url, "verify_tls": False, "api_key": {"$ref": "vault:fw/opnsense#key"}, "api_secret": {"$ref": "vault:fw/opnsense#nope"}}})
        assert r.status_code == 422 and "no secret fw/opnsense#nope" in str(r.get_json())
        r = admin.put("/api/integrations/fw", json={"enabled": True, "settings": {
            "url": opn.url, "verify_tls": False, "api_key": {"$ref": "vault:fw/opnsense#key"}, "api_secret": {"$ref": "vault:fw/opnsense#secret"}}})
        assert r.status_code == 200, r.get_json()
        assert run(admin, "fw", "test")["status"] == "succeeded"
        e = admin.get("/api/vault/entries").get_json()["entries"][0]
        assert admin.get(f"/api/vault/entries/{e['uid']}").get_json()["used_by"] == ["fw"]
        assert admin.delete(f"/api/vault/entries/{e['uid']}").status_code == 409
        # sealed: the job says why
        keyvault.seal()
        job = run(admin, "fw", "test")
        assert job["status"] == "failed" and "sealed" in (job.get("error") or str(job))
        admin.post("/api/vault/unseal", json={"passphrase": "test vault passphrase"})
        # renaming the entry takes the references along
        assert admin.put(f"/api/vault/entries/{e['uid']}", json={"path": "fw/edge"}).status_code == 200
        refs = admin.get("/api/integrations/fw").get_json()["secret_refs"]
        assert refs == {"api_key": "vault:fw/edge#key", "api_secret": "vault:fw/edge#secret"}
        assert run(admin, "fw", "test")["status"] == "succeeded"
    finally:
        opn.srv.close()


def test_pointing_at_a_secret_needs_apply(vault, make_user):
    admin = vault
    entry(admin, "wifi/iot", {"psk": "hunter2hunter2"})
    entry(admin, "dns/key", {"value": "k"}, host="srv-vm-dns-01")
    ed = make_user("ed", role="editor")  # edits hosts, may not reveal secrets
    h = host(ed, "srv-vm-dns-01")
    r = ed.put("/api/hosts/srv-vm-dns-01", json={**h, "vars": {"psk": {"$secret": "vault:wifi/iot#psk"}}})
    assert r.status_code == 422 and "needs secret:apply" in str(r.get_json())
    # what an admin pointed at stays when the editor saves the host
    put_host(admin, "srv-vm-dns-01", vars={"psk": {"$secret": "vault:wifi/iot#psk"}})
    h = host(ed, "srv-vm-dns-01")
    assert ed.put("/api/hosts/srv-vm-dns-01", json={**h, "notes": "edited"}).status_code == 200
    # a secret keeper of the host's zone may point at the host's own secret
    keeper = make_user("k", role="editor")
    admin.post("/api/admin/bindings", json={"user": "k", "role": "secret-keeper", "scopes": ["zone:DC_TC"]})
    h = host(keeper, "srv-vm-dns-01")
    assert keeper.put("/api/hosts/srv-vm-dns-01", json={**h, "vars": {**h["vars"], "key": {"$secret": "vault:dns/key"}}}).status_code == 200
    assert [x["ref"] for x in keeper.get("/api/vault/refs").get_json()["refs"]] == ["vault:dns/key#value"]
    # a reference that doesn't exist yet is allowed in variables, and lint points at it
    put_host(admin, "srv-vm-dns-01", vars={"later": {"$secret": "vault:not/yet#x"}})
    lint = admin.get("/api/lint").get_json()
    assert any(i["code"] == "secret-missing" and "not/yet#x" in i["message"] for i in lint["issues"])


def test_older_secrets_move_in_on_unseal(admin):
    keyvault.seal()
    fernet = current_app.extensions["labdex"]["vault"]
    db.session.add(Secret(name="grafana-admin", description="Grafana", value=fernet.encrypt("correct horse")))
    db.session.commit()
    admin.post("/api/features", json={"path": "grafana", "vars": {"pw": {"$secret": "grafana-admin"}}})
    from labdex import secretstore
    assert secretstore.reveal("grafana-admin") == "correct horse"  # still from the old table
    assert admin.get("/api/vault").get_json()["legacy"] == 1
    try:
        assert admin.post("/api/vault/init", json={"passphrase": "test vault passphrase"}).status_code == 200
        assert db.session.scalar(db.select(Secret)) is None
        e = db.session.scalar(db.select(VaultEntry).where(VaultEntry.path == "global/grafana-admin"))
        assert e.description == "Grafana" and keyvault.read(e) == {"value": "correct horse"}
        assert secretstore.reveal("grafana-admin") == "correct horse"  # the bare name means global/<name>#value
        assert secretstore.used_by("global/grafana-admin") == ["feature grafana"]
        hist = admin.get("/api/history?object_type=setting").get_json()
        assert any("moved 1 global secret" in x["summary"] for x in hist)
    finally:
        keyvault.seal()
