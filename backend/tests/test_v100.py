"""1.0: bundled official plugins, the official repository out of the box, SSE limits, throttling."""
import io
import shutil
import zipfile

from labdex.plugins import installer, trust
from labdex.models import PluginRepository, db

from .test_v04 import upload


def zip_dir(d, top):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(d.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, f"{top}/{p.relative_to(d)}")
    return buf.getvalue()


def test_bundled_plugins_update_and_fall_back(admin, tmp_path, monkeypatch):
    p = admin.get("/api/plugins/beszel").get_json()
    assert p["bundled"] and not p["builtin"] and p["trust"]["status"] == "official"
    # since 1.21 removing a shipped plugin only uninstalls it (the files stay); install it again
    assert admin.delete("/api/plugins/beszel").status_code == 204
    assert admin.get("/api/plugins/beszel").get_json()["installed"] is False
    assert admin.post("/api/marketplace/install", json={"id": "beszel"}).status_code == 201
    # an update: a newer copy in the plugin folder takes its place
    src = tmp_path / "beszel"
    shutil.copytree(installer.Path(p["path"]), src)
    y = src / "plugin.yaml"
    y.write_text(y.read_text().replace(f'version: "{p["version"]}"', 'version: "9.9"'))
    # only an update signed with the project key may stand in for a shipped plugin
    assert "labdex project key" in upload(admin, zip_dir(src, "beszel")).get_json()["error"]
    off = tmp_path / "official"
    off.mkdir()
    pem, pub = trust.keygen()
    (off / "test-project.pub").write_text(pub)
    monkeypatch.setattr(trust, "OFFICIAL_DIR", off)
    trust.sign(src, pem, "labdex project")
    info = upload(admin, zip_dir(src, "beszel")).get_json()
    assert info["trust"]["status"] == "official"
    assert admin.post(f"/api/plugins/stage/{info['token']}/confirm").status_code == 201
    p2 = admin.get("/api/plugins/beszel").get_json()
    assert p2["version"] == "9.9" and p2["replaces_bundled"] == p["version"]
    # removing the update brings the bundled one back, even while integrations use it
    assert admin.delete("/api/plugins/beszel").status_code == 204
    assert admin.get("/api/plugins/beszel").get_json()["version"] == p["version"]


def test_official_repository_once(app, monkeypatch):
    monkeypatch.setenv("LABDEX_OFFICIAL_INDEX", "https://example.org/labdex/plugins/index.json")
    with app.app_context():
        installer.ensure_official_repository()
        repo = db.session.scalar(db.select(PluginRepository))
        assert repo.official and repo.url.startswith("https://example.org/") and repo.key_id in trust.official_keys()
        db.session.delete(repo)
        db.session.commit()
        installer.ensure_official_repository()  # removed stays removed
        assert db.session.scalar(db.select(PluginRepository)) is None
    monkeypatch.setenv("LABDEX_OFFICIAL_INDEX", "https://OWNER.github.io/labdex/plugins/index.json")
    assert trust.official_index_url() == ""


def test_login_throttle_per_user_and_proxy(app, monkeypatch):
    from .conftest import client
    app.config["LABDEX_TRUSTED_PROXIES"] = ["10.0.0.1/32"]
    c = client(app)
    c.post("/api/auth/setup", json={"username": "boss", "password": "the-right-password"})
    c.post("/api/auth/logout")
    # five wrong tries from one client behind the proxy …
    for _ in range(5):
        r = c.post("/api/auth/login", json={"username": "boss", "password": "nope"},
                   environ_base={"REMOTE_ADDR": "10.0.0.1"}, headers={"X-Forwarded-For": "192.0.2.7"})
        assert r.status_code == 401
    r = c.post("/api/auth/login", json={"username": "boss", "password": "the-right-password"},
               environ_base={"REMOTE_ADDR": "10.0.0.1"}, headers={"X-Forwarded-For": "192.0.2.7"})
    assert r.status_code == 429
    # … don't lock out another client behind the same proxy
    r = c.post("/api/auth/login", json={"username": "boss", "password": "the-right-password"},
               environ_base={"REMOTE_ADDR": "10.0.0.1"}, headers={"X-Forwarded-For": "192.0.2.8"})
    assert r.status_code == 200


def test_security_headers(admin):
    r = admin.get("/api/meta")
    assert "camera=()" in r.headers["Permissions-Policy"] and r.headers["X-Frame-Options"] == "DENY"
