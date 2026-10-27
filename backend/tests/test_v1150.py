"""1.15: the KV vault: sealed start, envelope encryption, rights, reveal with history."""
import pytest

from labdex import keyvault
from labdex.models import VaultEntry, VaultKey, VaultVersion, db

PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def sealed():
    keyvault.seal()
    keyvault._tries.clear()
    yield
    keyvault.seal()


def setup_vault(admin):
    r = admin.post("/api/vault/init", json={"passphrase": PASS})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def confirm(c, password="correct-horse-battery"):
    return c.post("/api/auth/confirm", json={"password": password})


def test_set_up_seal_unseal(admin):
    st = admin.get("/api/vault").get_json()
    assert st["initialized"] is False and st["sealed"] is True and st["can_unseal"]
    assert admin.post("/api/vault/init", json={"passphrase": "short"}).status_code == 422
    assert setup_vault(admin) == {"initialized": True, "sealed": False}
    assert admin.post("/api/vault/init", json={"passphrase": PASS}).status_code == 409  # once
    row = db.session.scalar(db.select(VaultKey))
    assert PASS not in row.wrapped and len(row.wrapped) > 40  # only the wrapped master key is stored
    admin.post("/api/vault/seal")
    assert admin.get("/api/vault").get_json()["sealed"] is True
    assert admin.post("/api/vault/unseal", json={"passphrase": "not it at all, no"}).status_code == 403
    assert admin.post("/api/vault/unseal", json={"passphrase": PASS}).get_json()["sealed"] is False
    hist = [e["summary"] for e in admin.get("/api/history?object_name=vault").get_json()]
    assert "unsealed the vault" in hist and "unseal refused: wrong passphrase" in hist


def test_unseal_is_throttled(admin):
    setup_vault(admin)
    admin.post("/api/vault/seal")
    for _ in range(5):
        admin.post("/api/vault/unseal", json={"passphrase": "wrong wrong wrong"})
    assert admin.post("/api/vault/unseal", json={"passphrase": PASS}).status_code == 429


def test_entries_versions_and_reveal(admin):
    setup_vault(admin)
    r = admin.post("/api/vault/entries", json={"path": "Hosts/srv-pm-pve-01/root", "host": "srv-pm-pve-01", "description": "console",
                                               "fields": {"username": "root", "password": "hunter2-but-longer"}})
    assert r.status_code == 201, r.get_json()
    e = r.get_json()
    assert e["path"] == "hosts/srv-pm-pve-01/root" and sorted(e["fields"]) == ["password", "username"] and e["host"] == "srv-pm-pve-01"
    assert "hunter2" not in str(e)
    row = db.session.scalar(db.select(VaultVersion))
    assert "hunter2" not in row.ciphertext and "root" not in row.ciphertext
    # revealing needs the password confirmed in a browser session
    r = admin.post(f"/api/vault/entries/{e['uid']}/reveal", json={"field": "password"})
    assert r.status_code == 403
    assert confirm(admin, "wrong").status_code == 422
    assert confirm(admin).status_code == 204
    r = admin.post(f"/api/vault/entries/{e['uid']}/reveal", json={"field": "password"})
    assert r.get_json() == {"field": "password", "value": "hunter2-but-longer", "version": 1}
    # a new version keeps the field sent as null, drops what's left out
    r = admin.put(f"/api/vault/entries/{e['uid']}", json={"fields": {"username": None, "password": "new-and-better-one"}})
    assert r.status_code == 200 and r.get_json()["version"] == 2
    assert admin.post(f"/api/vault/entries/{e['uid']}/reveal", json={"field": "username"}).get_json()["value"] == "root"
    old = admin.post(f"/api/vault/entries/{e['uid']}/reveal", json={"field": "password", "version": 1}).get_json()
    assert old["value"] == "hunter2-but-longer"
    d = admin.get(f"/api/vault/entries/{e['uid']}").get_json()
    assert [v["version"] for v in d["versions"]] == [2, 1]
    # the host's entries; the history never holds a value
    assert [x["path"] for x in admin.get("/api/vault/entries?host=srv-pm-pve-01").get_json()["entries"]] == ["hosts/srv-pm-pve-01/root"]
    hist = admin.get("/api/history?object_type=secret").get_json()
    assert any("revealed hosts/srv-pm-pve-01/root#password" in h["summary"] for h in hist)
    assert "hunter2" not in str(hist) and "new-and-better" not in str(hist)
    # sealed: names are there, values aren't
    admin.post("/api/vault/seal")
    assert sorted(admin.get("/api/vault/entries").get_json()["entries"][0]["fields"]) == ["password", "username"]
    assert admin.post(f"/api/vault/entries/{e['uid']}/reveal", json={"field": "password"}).status_code == 423
    assert admin.post("/api/vault/entries", json={"path": "x/y", "fields": {"a": "b"}}).status_code == 423
    assert admin.put(f"/api/vault/entries/{e['uid']}", json={"description": "still fine sealed"}).status_code == 200


def test_tampering_and_swapping_are_noticed(admin):
    setup_vault(admin)
    confirm(admin)
    a = admin.post("/api/vault/entries", json={"path": "a", "fields": {"pw": "aaaaaaaa"}}).get_json()
    b = admin.post("/api/vault/entries", json={"path": "b", "fields": {"pw": "bbbbbbbb"}}).get_json()
    va = db.session.scalar(db.select(VaultVersion).join(VaultEntry).where(VaultEntry.uid == a["uid"]))
    vb = db.session.scalar(db.select(VaultVersion).join(VaultEntry).where(VaultEntry.uid == b["uid"]))
    va.wrapped_key, va.ciphertext = vb.wrapped_key, vb.ciphertext  # b's blob under a's name
    db.session.commit()
    assert admin.post(f"/api/vault/entries/{a['uid']}/reveal", json={"field": "pw"}).status_code == 500


def test_passphrase_change_keeps_the_entries(admin):
    setup_vault(admin)
    confirm(admin)
    e = admin.post("/api/vault/entries", json={"path": "wifi/iot", "fields": {"psk": "abcdefgh12"}}).get_json()
    assert admin.post("/api/vault/passphrase", json={"current": "nope nope nope", "new": "another long passphrase"}).status_code == 403
    assert admin.post("/api/vault/passphrase", json={"current": PASS, "new": "another long passphrase"}).status_code == 200
    admin.post("/api/vault/seal")
    assert admin.post("/api/vault/unseal", json={"passphrase": PASS}).status_code == 403
    assert admin.post("/api/vault/unseal", json={"passphrase": "another long passphrase"}).status_code == 200
    assert admin.post(f"/api/vault/entries/{e['uid']}/reveal", json={"field": "psk"}).get_json()["value"] == "abcdefgh12"


def test_rights(admin, make_user):
    setup_vault(admin)
    admin.post("/api/vault/entries", json={"path": "hosts/srv-pm-pve-01/root", "host": "srv-pm-pve-01", "fields": {"pw": "x" * 12}})
    admin.post("/api/vault/entries", json={"path": "wifi/iot", "fields": {"psk": "y" * 12}})
    # "*" never reaches revealing or changing: an admin role and a viewer see names only
    for role in ("viewer", "admin"):
        u = make_user(f"u{role}", role=role)
        entries = u.get("/api/vault/entries").get_json()["entries"]
        assert {x["path"] for x in entries} == {"hosts/srv-pm-pve-01/root", "wifi/iot"}
        assert not any(x["can_reveal"] or x["can_edit"] for x in entries)
        assert u.post(f"/api/vault/entries/{entries[0]['uid']}/reveal", json={"field": "pw"}).status_code == 403
        assert u.post("/api/vault/entries", json={"path": "mine/x", "fields": {"a": "b"}}).status_code == 403
        assert u.post("/api/vault/unseal", json={"passphrase": PASS}).status_code == 403
    # a secret-keeper for the datacenter reveals its hosts' secrets, not the Wi-Fi
    k = make_user("keeper", role="secret-keeper", scopes=["zone:DC_*"])
    entries = {x["path"]: x for x in k.get("/api/vault/entries").get_json()["entries"]}
    assert set(entries) == {"hosts/srv-pm-pve-01/root"} and entries["hosts/srv-pm-pve-01/root"]["can_reveal"]
    # by path: name scopes
    w = make_user("wifi", role="secret-keeper", scopes=["name:wifi/*"])
    assert [x["path"] for x in w.get("/api/vault/entries").get_json()["entries"]] == ["wifi/iot"]
    assert w.post("/api/vault/entries", json={"path": "hosts/x/y", "fields": {"a": "b"}}).status_code == 403
    assert w.post("/api/vault/entries", json={"path": "wifi/guest", "fields": {"psk": "z" * 12}}).status_code == 201


def test_entries_follow_their_host(admin):
    setup_vault(admin)
    e = admin.post("/api/vault/entries", json={"path": "bmc/pve", "host": "srv-pm-pve-01", "fields": {"pw": "q" * 12}}).get_json()
    from labdex import inventory
    inventory.import_dict(inventory.export_dict())
    assert admin.get(f"/api/vault/entries/{e['uid']}").get_json()["host"] == "srv-pm-pve-01"
    admin.delete("/api/hosts/srv-pm-pve-01")
    assert admin.get(f"/api/vault/entries/{e['uid']}").get_json()["host"] == ""
