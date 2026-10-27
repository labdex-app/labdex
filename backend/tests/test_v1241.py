"""1.24.1: no plugin ships with labdex; backups are part of the core; the plugins a 1.24.0
instance had come back from the official Marketplace by themselves."""
from labdex.models import Integration, PluginRepository, db
from labdex.plugins import Registry, catalog, installer, trust

from .test_v1230 import add_repo, publish
from .test_v1230 import repo  # noqa: F401  (a fixture)


def test_labdex_alone_has_only_backups(monkeypatch):
    monkeypatch.delenv("LABDEX_DEV_PLUGINS", raising=False)
    reg = Registry(None)
    assert sorted(reg.specs) == ["backup"] and reg.get("backup").builtin and not reg.errors


def _official(admin, repo, monkeypatch):  # noqa: F811
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0")
    rid = add_repo(admin, repo["url"])
    r = db.session.get(PluginRepository, rid)
    kid, pub = r.key_id, trust.load_public(r.public_key)
    monkeypatch.setattr(trust, "official_keys", lambda: {kid: ("labdex project", pub)})
    r.official = True
    db.session.commit()
    installer._index_cache.clear()
    return rid


def test_plugins_of_an_upgraded_instance_come_back(admin, repo, monkeypatch):  # noqa: F811
    _official(admin, repo, monkeypatch)
    # 1.24.0 had hello installed (it shipped with labdex) and an instance of it
    catalog._save(catalog.wanted() | {"hello"})
    db.session.add(Integration(plugin_id="hello", name="h", enabled=True, settings={}, state={"kept": 1}, selectors=[]))
    db.session.commit()
    miss = admin.get("/api/plugins").get_json()["missing"]
    assert miss == [{"id": "hello", "instances": ["h"]}]

    r = admin.post("/api/plugins/restore").get_json()
    assert r["installed"] == ["hello"] and not r["failed"] and r["missing"] == []
    assert admin.get("/api/plugins").get_json()["missing"] == []
    assert "hello" in {p["id"] for p in admin.get("/api/plugins").get_json()["plugins"]}
    assert "hello" not in catalog.wanted()  # installed for real now: the old list is done with it
    assert db.session.scalar(db.select(Integration).where(Integration.name == "h")).state == {"kept": 1}
    assert any("again from" in h["summary"] for h in admin.get("/api/history?object_type=plugin").get_json())

    # removed on purpose: not put back
    assert admin.delete("/api/integrations/h").status_code == 204
    assert admin.delete("/api/plugins/hello").status_code == 204
    assert installer.missing() == []


def test_only_from_the_official_repository(admin, repo):  # noqa: F811
    publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0")
    add_repo(admin, repo["url"])  # a community repository offering it
    catalog._save(catalog.wanted() | {"hello"})
    db.session.commit()
    r = admin.post("/api/plugins/restore").get_json()
    assert r["installed"] == [] and "official repository" in r["failed"]["hello"]
    assert r["missing"] == ["hello"]


def test_retries_wait_between_tries(app, monkeypatch):
    catalog._save(catalog.wanted() | {"gone"})
    db.session.commit()
    installer._restore_tried.clear()
    assert installer.restore_missing()["failed"]  # tried (no official repository)
    assert installer.restore_missing()["failed"] == {}  # too soon: not tried again
    assert installer.restore_missing(force=True)["failed"]


def test_backup_is_never_installed_from_a_repository(admin, repo):  # noqa: F811
    rid = add_repo(admin, (publish(repo["tmp"], repo["src"], repo["out"], repo["pem"], "1.0") and repo["url"]))
    r = admin.post("/api/marketplace/install", json={"repository_id": rid, "id": "backup"})
    assert r.status_code == 422 and "part of labdex" in r.get_json()["error"]


def test_an_installed_backup_copy_gives_way_to_the_core_one(tmp_path):
    import shutil
    from labdex.plugins import BUILTIN_DIR
    shutil.copytree(BUILTIN_DIR / "backup", tmp_path / "p" / "backup")
    reg = Registry(str(tmp_path / "p"))
    assert reg.get("backup").builtin and not reg.errors


def test_a_moved_default_follows_unless_set(app):
    """1.24.1: the project moved to labdex-app; instances with the old default follow it."""
    from labdex import naming
    from labdex.models import Setting
    db.session.get(Setting, "project_url").value = "https://github.com/reykimedev/labdex"
    db.session.commit()
    naming.ensure_defaults_in_session()
    assert naming.get_setting("project_url") == "https://github.com/labdex-app/labdex"
    db.session.get(Setting, "project_url").value = "https://git.lab.example/me/labdex"
    naming.ensure_defaults_in_session()
    assert naming.get_setting("project_url") == "https://git.lab.example/me/labdex"


def test_permission_checks_need_only_the_host_basics(admin):
    """store.host_access gives rbac.host_facts what host_to_dict would."""
    from labdex import rbac, store
    from labdex.models import Host
    for h in db.session.scalars(db.select(Host)):
        a, b = rbac.host_facts(store.host_access(h)), rbac.host_facts(store.host_to_dict(h))
        assert (a.name, a.names, a.zones, a.areas, a.networks, a.tags, a.seen_zones, a.seen_areas) == (b.name, b.names, b.zones, b.areas, b.networks, b.tags, b.seen_zones, b.seen_areas), h.name
