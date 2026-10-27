"""1.23: plugin versions: changelogs, older versions, rollback, holding a version,
withdrawn versions and the update check (announce, or install official updates)."""
import json
import shutil

import pytest
from flask import Response

from labdex.plugins import installer, trust, updates

from .fakes import FakeServer
from .test_v04 import make_plugin


def publish(tmp_path, src, out, pem, version, notes=""):
    """Put hello <version> in src (with a CHANGELOG.md) and rebuild the repository in out."""
    shutil.rmtree(src / "hello", ignore_errors=True)
    d = make_plugin(tmp_path / f"v{version}", version)
    shutil.copytree(d, src / "hello")
    log = (src / "CHANGELOG.md")
    log.write_text((log.read_text() if log.exists() else "# Changelog\n") + f"\n## {version}\n{notes or 'Changes in ' + version}\n")
    shutil.copy(log, src / "hello" / "CHANGELOG.md")
    return installer.build_repository(src, out, pem, "Test repo")


@pytest.fixture
def repo(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    pem, _ = trust.keygen()

    def handle(req):
        f = out / req.path.lstrip("/")
        return Response(f.read_bytes(), mimetype="application/octet-stream") if f.is_file() else Response("", 404)
    srv = FakeServer(handle)
    yield {"src": src, "out": out, "pem": pem, "url": srv.url + "/index.json", "tmp": tmp_path}
    srv.close()


def add_repo(admin, url):
    key = admin.post("/api/plugin-repos/preview", json={"url": url}).get_json()["key_id"]
    return admin.post("/api/plugin-repos", json={"url": url, "key_id": key}).get_json()["id"]


def install(admin, rid, version=None):
    info = admin.post("/api/marketplace/install", json={"repository_id": rid, "id": "hello", **({"version": version} if version else {})}).get_json()
    assert "token" in info, info
    r = admin.post(f"/api/plugins/stage/{info['token']}/confirm")
    assert r.status_code == 201, r.get_json()
    return info


def market(admin):
    installer._index_cache.clear()
    return next(p for p in admin.get("/api/marketplace").get_json()["plugins"] if p["id"] == "hello")


def test_versions_changelog_rollback_hold(admin, repo):
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0", "First.")
    idx = publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.1", "Faster.")
    hello = idx["plugins"][0]
    assert hello["version"] == "1.1" and [v["version"] for v in hello["versions"]] == ["1.1", "1.0"]
    assert hello["changes"] == "Faster." and hello["versions"][1]["changes"] == "First."
    rid = add_repo(admin, repo["url"])

    install(admin, rid, "1.0")  # an older version on purpose
    m = market(admin)
    assert m["installed_version"] == "1.0" and m["update"] and m["repo_version"] == "1.1"
    assert m["changes"] == "1.1: Faster." and [v["version"] for v in m["versions"]] == ["1.1", "1.0"]
    assert m["previous"] is None

    install(admin, rid)  # the update
    m = market(admin)
    assert m["installed_version"] == "1.1" and not m["update"] and m["previous"] == "1.0"

    # roll back (the replaced copy was kept), and roll back the rollback
    r = admin.post("/api/plugins/hello/rollback")
    assert r.status_code == 200 and r.get_json() == {"id": "hello", "version": "1.0", "previous": "1.1"}
    assert admin.get("/api/plugins/hello").get_json()["version"] == "1.0"
    assert admin.post("/api/plugins/hello/rollback").get_json()["version"] == "1.1"
    assert any("rolled hello back from 1.0 to 1.1" in h["summary"] for h in admin.get("/api/history?object_type=plugin").get_json())

    # hold: no update offered while held
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.2", "Shiny.")
    assert market(admin)["update"] is True
    assert admin.put("/api/plugins/hello/hold", json={"version": "1.1"}).get_json() == {"id": "hello", "held": "1.1"}
    m = market(admin)
    assert m["update"] is False and m["held"] == "1.1"
    admin.put("/api/plugins/hello/hold", json={"version": None})
    assert market(admin)["update"] is True

    # uninstalling removes the kept copy too
    assert admin.delete("/api/plugins/hello").status_code == 204
    assert installer.previous_version("hello") is None


def test_withdrawn_versions(admin, repo):
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0")
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.1")
    rid = add_repo(admin, repo["url"])
    install(admin, rid, "1.0")
    installer.yank(repo["out"], repo["pem"], "hello", "1.1", "eats the database")
    m = market(admin)
    assert m["update"] is False and m["repo_version"] == "1.0" and m["latest_version"] == "1.1"
    assert next(v for v in m["versions"] if v["version"] == "1.1")["yanked"] == "eats the database"
    # the installed one withdrawn: flagged, and the newest good one is offered
    installer.yank(repo["out"], repo["pem"], "hello", "1.1", "")
    installer.yank(repo["out"], repo["pem"], "hello", "1.0", "bad")
    m = market(admin)
    assert m["withdrawn"] == "bad" and m["update"] is True and m["repo_version"] == "1.1"
    # a yanked index is still signed by the repository's key
    assert installer.fetch_repository(installer.db.session.get(installer.PluginRepository, rid), force=True)
    with pytest.raises(installer.InstallError):
        installer.yank(repo["out"], repo["pem"], "hello", "9.9", "nope")


def test_update_check_announces_once(admin, repo, app):
    from labdex.models import Notification, NotificationChannel, db
    admin.post("/api/notification-channels", json={"name": "hook", "kind": "webhook", "events": ["plugins.*"], "settings": {"url": "http://127.0.0.1:9/x"}})
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0")
    rid = add_repo(admin, repo["url"])
    install(admin, rid)
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.1", "Better.")
    installer._index_cache.clear()
    r = admin.post("/api/plugin-updates/check").get_json()
    assert r["announced"] == ["hello"] and r["installed"] == [] and r["mode"] == "notify"
    titles = [n.title for n in db.session.scalars(db.select(Notification))]
    assert "hello 1.1 is available" in " ".join(titles) or any("1.1 is available" in t for t in titles), titles
    assert admin.post("/api/plugin-updates/check").get_json()["announced"] == []  # once
    # "official" mode only installs what the labdex project signed: this repository isn't official
    assert admin.put("/api/plugin-updates", json={"mode": "official"}).get_json()["mode"] == "official"
    assert admin.put("/api/plugin-updates", json={"mode": "always"}).status_code == 422
    r = admin.post("/api/plugin-updates/check").get_json()
    assert r["installed"] == [] and admin.get("/api/plugins/hello").get_json()["version"] == "1.0"
    # the scheduler's call is rate-limited
    with app.app_context():
        assert updates.check()["skipped"] is True
    assert NotificationChannel


def test_official_updates_install_themselves(admin, repo, monkeypatch):
    from labdex.models import PluginRepository, db
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0")
    rid = add_repo(admin, repo["url"])
    install(admin, rid)
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.1")
    # pretend this repository's key is the project key
    kid = db.session.get(PluginRepository, rid).key_id
    pub = trust.load_public(db.session.get(PluginRepository, rid).public_key)
    monkeypatch.setattr(trust, "official_keys", lambda: {kid: ("labdex project", pub)})
    db.session.get(PluginRepository, rid).official = True
    db.session.commit()
    installer._index_cache.clear()
    admin.put("/api/plugin-updates", json={"mode": "official"})
    r = admin.post("/api/plugin-updates/check").get_json()
    assert r["installed"] == ["hello"], r
    assert admin.get("/api/plugins/hello").get_json()["version"] == "1.1"
    assert any("by itself" in h["summary"] for h in admin.get("/api/history?object_type=plugin").get_json())
    # a held plugin isn't touched
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.2")
    installer._index_cache.clear()
    admin.put("/api/plugins/hello/hold", json={"version": "1.1"})
    assert admin.post("/api/plugin-updates/check").get_json()["installed"] == []


def test_settings_survive_an_import(admin):
    admin.put("/api/plugin-updates", json={"mode": "off"})
    admin.put("/api/plugins/backup/hold", json={"version": "1.2"})
    y = admin.get("/api/admin/backup").get_data(as_text=True)
    assert admin.post("/api/admin/restore", json={"yaml": y, "confirm": "replace"}).status_code == 200
    d = admin.get("/api/plugin-updates").get_json()
    assert d["mode"] == "off" and d["holds"] == {"backup": "1.2"}


def test_changes_for():
    log = "# Changelog\n\n## 1.2\n- new\n\n## [1.1] - 2026-01-01\nfix\n\n## 1.0\nfirst\n"
    assert installer.changes_for(log, "1.2") == "- new"
    assert installer.changes_for(log, "1.1") == "fix"
    assert installer.changes_for(log, "1.0") == "first"
    assert installer.changes_for(log, "1.10") == ""
    assert json.dumps(installer.versions_of({"version": "2", "url": "a", "versions": [{"version": "10", "url": "b"}, {"version": "2", "url": "a"}]})).count('"version"') == 2


def test_index_from_plugin_repositories(admin, tmp_path):
    """A Marketplace whose plugins live in their own git repositories: releases are versions."""
    pem, _ = trust.keygen()
    other, _ = trust.keygen()
    assets = {}
    for v, key in (("1.0", pem), ("1.1", pem), ("1.2", other)):  # 1.2 is signed with a stranger's key: left out
        d = make_plugin(tmp_path / f"r{v}", v)
        trust.sign(d, key, "someone")
        buf = tmp_path / f"hello-{v}.zip"
        import zipfile
        with zipfile.ZipFile(buf, "w") as zf:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    zf.write(f, f"hello/{f.relative_to(d).as_posix()}")
        assets[f"hello-{v}.zip"] = buf.read_bytes()

    def handle(req):
        if req.path == "/repos/me/labdex-plugin-hello/releases":
            return Response(json.dumps([{"tag_name": f"v{v}", "body": f"notes {v}", "published_at": f"2026-10-0{i}T10:00:00Z", "draft": False, "prerelease": False,
                                         "assets": [{"name": f"hello-{v}.zip", "browser_download_url": f"{srv.url}/dl/hello-{v}.zip"}]}
                                        for i, v in enumerate(["1.0", "1.1", "1.2"], 1)]), mimetype="application/json")
        if req.path == "/users/me/repos":  # discovery finds the listed one again and an empty new one
            return Response(json.dumps([{"name": "labdex-plugin-hello", "full_name": "me/labdex-plugin-hello"},
                                        {"name": "labdex-plugin-new", "full_name": "me/labdex-plugin-new"}, {"name": "other", "full_name": "me/other"}]),
                            mimetype="application/json")
        if req.path == "/repos/me/labdex-plugin-new/releases":
            return Response("[]", mimetype="application/json")
        if req.path.startswith("/dl/"):
            return Response(assets[req.path[4:]], mimetype="application/zip")
        return Response("", 404)
    srv = FakeServer(handle)
    try:
        sources = {"name": "Mine", "plugins": [{"repo": "me/labdex-plugin-hello", "withdrawn": {"1.0": "old bug"}}, {"repo": "me/missing"}],
                   "discover": [{"owner": "me", "prefix": "labdex-plugin-"}]}
        doc = installer.build_index_from_sources(sources, tmp_path / "out", pem, api=srv.url)
        hello = doc["plugins"][0]
        assert hello["version"] == "1.1" and [v["version"] for v in hello["versions"]] == ["1.1", "1.0"]
        assert hello["versions"][1]["yanked"] == "old bug" and hello["changes"] == "notes 1.1" and hello["date"] == "2026-10-02"
        assert hello["url"] == f"{srv.url}/dl/hello-1.1.zip" and hello["source"] == "https://github.com/me/labdex-plugin-hello"
        assert any("hello-1.2.zip" in p and "not signed with this repository's key" in p for p in doc["problems"])
        assert any(p.startswith("me/missing") for p in doc["problems"])
        assert "me/labdex-plugin-new: no release with a signed plugin" in doc["problems"] and len(doc["plugins"]) == 1
        trust.verify_index(json.loads((tmp_path / "out" / "index.json").read_text()), trust.load_public(doc["public_key"]))

        # labdex reads it like any repository: absolute asset URLs, versions, withdrawn ones
        (tmp_path / "srvroot").mkdir()
        idx = (tmp_path / "out" / "index.json").read_text()
        srv2 = FakeServer(lambda req: Response(idx, mimetype="application/json"))
        try:
            rid = add_repo(admin, srv2.url + "/index.json")
            install(admin, rid)
            m = market(admin)
            assert m["installed_version"] == "1.1" and m["source_url"] == "https://github.com/me/labdex-plugin-hello"
        finally:
            srv2.close()
    finally:
        srv.close()


def test_official_repository_follows_the_shipped_key(app, monkeypatch):
    """A Marketplace that moved or got a new key: installs that had the old one switch over."""
    from labdex.models import PluginRepository, db
    old, _ = trust.keygen()
    from cryptography.hazmat.primitives import serialization
    oldpub = serialization.load_pem_private_key(old, None).public_key()
    with app.app_context():
        db.session.add(PluginRepository(name="labdex official", url="https://x.github.io/labdex/plugins/index.json", key_id=trust.key_id(oldpub),
                                        public_key=trust.public_b64(oldpub), official=True, added_by="labdex"))
        db.session.commit()
        monkeypatch.setenv("LABDEX_OFFICIAL_INDEX", "https://x.github.io/labdex-plugins/index.json")
        installer.ensure_official_repository()
        r = db.session.scalar(db.select(PluginRepository).where(PluginRepository.official.is_(True)))
        kid = next(iter(trust.official_keys()))
        assert (r.url, r.key_id) == ("https://x.github.io/labdex-plugins/index.json", kid)
        assert db.session.scalar(db.select(db.func.count()).select_from(PluginRepository)) == 1


def test_releaseignore(tmp_path):
    root = tmp_path / "p"
    for f in ["plugin.yaml", "plugin.py", "CHANGELOG.md", "README.md", "tests/test_x.py", "docs/draft.md", "docs/keep.md",
              "playbooks/site.yml", "debug.log", "sub/debug.log", ".github/workflows/release.yml", ".gitignore"]:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("x")
    (root / ".releaseignore").write_text("# not for users\ntests/\n*.log\ndocs/draft.md\n")
    gone = installer.prune_release(root)
    left = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert left == ["CHANGELOG.md", "README.md", "docs/keep.md", "playbooks/site.yml", "plugin.py", "plugin.yaml"], left
    assert ".releaseignore" in gone and "tests" in gone and "sub/debug.log" in gone
