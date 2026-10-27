"""1.3: every real plugin is an official one (bundled, updatable from the official repository)."""
from pathlib import Path

import yaml

from labdex.plugins import BUILTIN_DIR, BUNDLED_DIR, Registry, compatibility


def test_only_the_demo_lab_is_built_in():
    assert sorted(p.name for p in BUILTIN_DIR.iterdir() if (p / "plugin.yaml").is_file()) == ["demo-lab"]


def test_every_official_plugin_states_a_range_this_labdex_meets():
    for d in sorted(BUNDLED_DIR.iterdir()):
        mf = d / "plugin.yaml"
        if not mf.is_file():
            continue
        m = yaml.safe_load(mf.read_text())
        assert m.get("labdex"), f"{d.name}: no labdex: range"
        assert compatibility(m) == (True, ""), d.name
        assert m.get("version"), d.name


def test_formerly_built_in_plugins_load_as_official(admin):
    plugins = {p["id"]: p for p in admin.get("/api/plugins").get_json()["plugins"]}
    for pid in ("opnsense", "technitium", "nmap", "backup", "exports", "ansible", "traefik", "docker", "proxmox"):
        p = plugins[pid]
        assert p["bundled"] and not p["builtin"] and p["trust"]["status"] == "official" and not p["error"], pid


def test_official_plugins_use_no_private_core_names():
    """They update on their own, so they may only use names without a leading underscore."""
    import re
    rx = re.compile(r"from labdex[\w.]* import (?:[\w ]+, )*_\w+|labdex\.\w+\._\w+")
    for py in BUNDLED_DIR.rglob("*.py"):
        assert not rx.search(py.read_text()), py


def test_backup_still_created_on_first_start(tmp_path):
    from labdex import create_app
    from labdex.models import Integration, db
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'b.db'}", "SECRET_KEY": "t",
                      "LABDEX_PLUGIN_DIR": str(tmp_path / "p"), "LABDEX_SCHEDULER": False, "LABDEX_AUTO_BACKUP": True})
    with app.app_context():
        assert db.session.scalar(db.select(Integration).where(Integration.plugin_id == "backup")) is not None


def test_registry_has_no_duplicates():
    reg = Registry(None)
    assert not reg.errors and "opnsense" in reg.specs and Path(reg.get("opnsense").path).parent == BUNDLED_DIR


def test_only_official_updates_replace_a_shipped_plugin(tmp_path):
    """A copy of a shipped plugin in the plugin folder that the project didn't sign is ignored."""
    import shutil
    fake = tmp_path / "plugins" / "nmap"
    shutil.copytree(BUNDLED_DIR / "nmap", fake, ignore=shutil.ignore_patterns("__pycache__"))
    (fake / "plugin.yaml").write_text((fake / "plugin.yaml").read_text().replace('version: "1.2"', 'version: "9.9"'))
    reg = Registry(str(tmp_path / "plugins"))
    spec = reg.get("nmap")
    assert spec.bundled and not spec.replaces_bundled and Path(spec.path).parent == BUNDLED_DIR
    assert any("labdex project key" in e["error"] for e in reg.errors)
