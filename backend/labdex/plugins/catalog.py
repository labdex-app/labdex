"""Which plugins this labdex is meant to have.

Plugins from a repository or an upload are installed by being in the plugin folder. Up to
1.24.0 the official plugins shipped inside labdex and were "installed" by being listed in the
setting below; since 1.24.1 they come from the Marketplace like every other plugin, and the
list is what to put back after the upgrade (installer.restore_missing). Development checkouts
(LABDEX_DEV_PLUGINS) still use it the old way.
"""
from __future__ import annotations

import json

from ..models import Integration, Setting, db

CORE = ("backup", "exports", "ansible", "nmap")
SETTING = "bundled_installed"


def installed_bundled() -> set[str]:
    row = db.session.get(Setting, SETTING)
    if row is None:  # never decided (a database older than the migration that sets it): what's in use, and the core
        used = {i.plugin_id for i in db.session.scalars(db.select(Integration))}
        return set(CORE) | used
    try:
        return set(json.loads(row.value or "[]"))
    except ValueError:
        return set(CORE)


def is_installed(spec) -> bool:
    if spec is None:
        return False
    if not spec.bundled or spec.replaces_bundled:
        return True  # built in, uploaded, from a repository, or an update of a bundled one
    return spec.id in installed_bundled()


def _save(ids: set[str]) -> None:
    row = db.session.get(Setting, SETTING)
    if row is None:
        row = Setting(key=SETTING)
        db.session.add(row)
    row.value = json.dumps(sorted(ids))


def install(pid: str) -> None:
    _save(installed_bundled() | {pid})


def wanted() -> set[str]:
    """The plugins listed as installed (not the defaults of a database that never decided)."""
    row = db.session.get(Setting, SETTING)
    try:
        return set(json.loads(row.value or "[]")) if row is not None else set()
    except ValueError:
        return set()


def forget(pid: str) -> None:
    if pid in wanted():
        _save(wanted() - {pid})


def uninstall(pid: str) -> None:
    """Refused while instances of it exist (they'd lose their plugin)."""
    used = [i.name for i in db.session.scalars(db.select(Integration).where(Integration.plugin_id == pid))]
    if used:
        raise ValueError(f"still used by {', '.join(used)}: delete those first")
    _save(installed_bundled() - {pid})
