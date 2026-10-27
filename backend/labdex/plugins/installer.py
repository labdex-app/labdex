"""Installing, upgrading and removing plugins: from an uploaded .zip or a signed repository.

Installs go through a staging step: the archive is unpacked safely, the manifest
validated and the signature checked *without running any plugin code*; the
administrator sees the result and confirms. Unsigned plugins need an explicit
"accept unsigned"; plugins with an invalid signature are refused.
"""
from __future__ import annotations

import hashlib
import io
import json
import secrets as pysecrets
import shutil
import time
import zipfile
from datetime import datetime, UTC
from pathlib import Path

import requests
import yaml
from flask import Flask, current_app

from ..models import Integration, PluginRepository, TrustedKey, db, utcnow
from . import Registry, _validate, capabilities_of, compatibility
from .trust import load_public, verify

MAX_ZIP = 20 * 1024 * 1024
MAX_FILES = 500
MAX_UNPACKED = 50 * 1024 * 1024
STAGE_TTL = 3600


class InstallError(Exception):
    pass


def community_keys() -> dict:
    """Keys trusted besides the official ones: added by hand, and those of the repositories."""
    out = {}
    for k in db.session.scalars(db.select(TrustedKey)):
        try:
            out[k.key_id] = (k.name, load_public(k.public_key))
        except (ValueError, TypeError):
            continue
    for r in db.session.scalars(db.select(PluginRepository).where(PluginRepository.enabled.is_(True))):
        try:
            out.setdefault(r.key_id, (r.name, load_public(r.public_key)))
        except (ValueError, TypeError):
            continue
    return out


def reload_registry(app: Flask | None = None) -> Registry:
    app = app or current_app._get_current_object()
    reg = Registry(app.config["LABDEX_PLUGIN_DIR"], community_keys(), demo=bool(app.config.get("LABDEX_DEMO")),
                   sandbox=app.config.get("LABDEX_PLUGIN_SANDBOX", "on"))
    app.extensions["labdex"]["registry"] = reg
    return reg


def _plugin_dir() -> Path:
    d = Path(current_app.config["LABDEX_PLUGIN_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage_root() -> Path:
    d = Path(current_app.config["LABDEX_DATA_DIR"]) / "plugin-staging"
    if current_app.config.get("TESTING"):
        d = _plugin_dir().parent / "plugin-staging"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():  # clean up abandoned stages
        if old.is_dir() and time.time() - old.stat().st_mtime > STAGE_TTL:
            shutil.rmtree(old, ignore_errors=True)
    return d


def _safe_extract(blob: bytes, dest: Path) -> Path:
    if len(blob) > MAX_ZIP:
        raise InstallError(f"archive is larger than {MAX_ZIP // 1024 // 1024} MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise InstallError("not a zip file") from exc
    infos = zf.infolist()
    if len(infos) > MAX_FILES:
        raise InstallError("too many files in the archive")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise InstallError("archive unpacks to too much data")
    written = 0
    for i in infos:
        name = i.filename
        if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
            raise InstallError(f"unsafe path in archive: {name}")
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise InstallError(f"symlinks aren't allowed: {name}")
        target = (dest / name).resolve()
        if not target.is_relative_to(dest.resolve()):
            raise InstallError(f"unsafe path in archive: {name}")
        if i.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(i) as src, open(target, "wb") as out:
            while chunk := src.read(65536):
                written += len(chunk)
                if written > MAX_UNPACKED:  # sizes in the zip's directory can lie
                    raise InstallError("archive unpacks to too much data")
                out.write(chunk)
    # plugin.yaml at the top, or inside a single top-level folder
    if (dest / "plugin.yaml").is_file():
        return dest
    subs = [p for p in dest.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))]
    if len(subs) == 1 and (subs[0] / "plugin.yaml").is_file():
        return subs[0]
    raise InstallError("no plugin.yaml in the archive")


def stage(blob: bytes, source: str = "upload") -> dict:
    token = pysecrets.token_hex(12)
    root = _stage_root() / token
    root.mkdir()
    try:
        pdir = _safe_extract(blob, root / "x")
        try:
            manifest = yaml.safe_load((pdir / "plugin.yaml").read_text()) or {}
        except yaml.YAMLError as exc:
            raise InstallError(f"plugin.yaml: {exc}") from exc
        problem = _validate(manifest, pdir)
        if problem:
            raise InstallError(problem)
        ok, why = compatibility(manifest)
        if not ok:
            raise InstallError(f"{manifest['id']} {manifest.get('version', '')}: {why}")
        trust = verify(pdir, manifest, community_keys())
    except InstallError:
        shutil.rmtree(root, ignore_errors=True)
        raise
    pid = str(manifest["id"])
    reg = current_app.extensions["labdex"]["registry"]
    cur = reg.get(pid)
    if cur is not None and cur.builtin:
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError(f"'{pid}' is a built-in plugin and can't be replaced")
    if cur is not None and (cur.bundled or cur.replaces_bundled) and trust.get("status") != "official":
        shutil.rmtree(root, ignore_errors=True)
        raise InstallError(f"'{pid}' ships with labdex: only an update signed with the labdex project key can replace it")
    info = {
        "token": token, "id": pid, "name": manifest.get("name", pid), "version": str(manifest.get("version", "")),
        "description": manifest.get("description", ""), "author": manifest.get("author", ""),
        "tags": manifest.get("tags") or [], "trust": trust, "source": source,
        "installed_version": cur.manifest.get("version", "") if cur else None,
        "python": (pdir / manifest.get("python", "plugin.py")).is_file(),
        "ansible": sorted({a["ansible"] for a in manifest.get("actions") or [] if a.get("ansible")}),
        "files": sorted(p.relative_to(pdir).as_posix() for p in pdir.rglob("*") if p.is_file()),
        "installable": trust["status"] != "invalid",
        "needs_confirmation": trust["status"] == "unsigned",
    }
    (root / "info.json").write_text(json.dumps({**info, "dir": str(pdir)}))
    return info


def confirm(token: str, accept_unsigned: bool) -> dict:
    root = _stage_root() / token
    if not token.isalnum() or not (root / "info.json").is_file():
        raise InstallError("this upload expired; upload it again")
    info = json.loads((root / "info.json").read_text())
    if not info["installable"]:
        raise InstallError(f"refused: {info['trust']['detail']}")
    if info["needs_confirmation"] and not accept_unsigned:
        raise InstallError("the plugin isn't signed; confirm that you trust its author")
    dest = _plugin_dir() / info["id"]
    backup = None
    if dest.exists():
        backup = dest.with_name(f".{info['id']}.replacing")
        shutil.rmtree(backup, ignore_errors=True)
        dest.rename(backup)
    shutil.move(info["dir"], dest)
    shutil.rmtree(root, ignore_errors=True)
    reg = reload_registry()
    spec = reg.get(info["id"])
    if spec is None or spec.error:
        # roll back to what was there
        shutil.rmtree(dest, ignore_errors=True)
        if backup is not None:
            backup.rename(dest)
        reload_registry()
        raise InstallError(f"installed, but it didn't load: {spec.error if spec else 'not found'}; rolled back")
    if backup is not None:  # kept as the version to roll back to (one step)
        prev = previous_dir(info["id"])
        shutil.rmtree(prev, ignore_errors=True)
        prev.parent.mkdir(parents=True, exist_ok=True)
        backup.rename(prev)
    return {**info, "installed": True}


def previous_dir(pid: str) -> Path:
    return _plugin_dir() / ".previous" / pid


def previous_version(pid: str) -> str | None:
    """The version an update replaced, kept to roll back to; None if there's none."""
    f = previous_dir(pid) / "plugin.yaml"
    if not f.is_file():
        return None
    try:
        return str((yaml.safe_load(f.read_text()) or {}).get("version", "")) or "?"
    except yaml.YAMLError:
        return None


def rollback(pid: str) -> dict:
    """Swap the installed copy with the one it replaced. The swapped-out copy becomes the
    previous one, so a rollback can be undone the same way. The old copy's signature is
    checked when it loads, like any other."""
    prev, dest = previous_dir(pid), _plugin_dir() / pid
    if not (prev / "plugin.yaml").is_file():
        raise InstallError(f"no earlier version of '{pid}' is kept")
    if not dest.is_dir():
        raise InstallError(f"'{pid}' isn't installed here")
    hold = dest.with_name(f".{pid}.replacing")
    shutil.rmtree(hold, ignore_errors=True)
    dest.rename(hold)
    prev.rename(dest)
    reg = reload_registry()
    spec = reg.get(pid)
    if spec is None or spec.error:
        dest.rename(prev)
        hold.rename(dest)
        reload_registry()
        raise InstallError(f"the earlier version doesn't load: {spec.error if spec else 'not found'}; nothing changed")
    hold.rename(prev)
    return {"id": pid, "version": str(spec.manifest.get("version", "")), "previous": previous_version(pid)}


def uninstall(pid: str, force: bool = False) -> None:
    reg = current_app.extensions["labdex"]["registry"]
    spec = reg.get(pid)
    if spec is None:
        raise InstallError(f"no plugin '{pid}'")
    if spec.builtin:
        raise InstallError("built-in plugins can't be removed; disable or delete their integrations instead")
    if spec.bundled:
        raise InstallError("this plugin ships with labdex and can't be removed; disable or delete its integrations instead")
    used = [i.name for i in db.session.scalars(db.select(Integration).where(Integration.plugin_id == pid))]
    if used and not force and not spec.replaces_bundled:  # removing an update falls back to the bundled copy
        raise InstallError(f"still used by {', '.join(used)}; delete those integrations first")
    root = Path(current_app.config["LABDEX_PLUGIN_DIR"]).resolve()
    target = spec.path.resolve()
    if not target.is_relative_to(root) or target == root:
        raise InstallError("plugin isn't in the plugin folder")
    shutil.rmtree(target)
    shutil.rmtree(previous_dir(pid), ignore_errors=True)
    reload_registry()


# ---------------------------------------------------------------- repositories
_index_cache: dict = {}


def _get_index(url: str) -> dict:
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise InstallError(f"{url}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise InstallError(f"{url}: not a labdex plugin index")
    return data


def preview_repository(url: str) -> dict:
    """What adding this repository would trust: its name and key (from the index, checked against itself)."""
    from .trust import key_id, load_public, official_keys, verify_index
    if not url.startswith(("https://", "http://")):
        raise InstallError("the index URL starts with https://")
    doc = _get_index(url)
    try:
        pub = load_public(str(doc.get("public_key", "")))
        verify_index(doc, pub)
    except (ValueError, TypeError) as exc:
        raise InstallError(f"{url}: {exc}") from exc
    kid = key_id(pub)
    return {"url": url, "name": str(doc.get("name") or url)[:64], "description": str(doc.get("description") or ""),
            "key_id": kid, "public_key": doc["public_key"], "official": kid in official_keys(),
            "plugins": len(doc["plugins"]), "generated": doc.get("generated")}


def fetch_repository(repo: PluginRepository, force: bool = False) -> dict:
    """The repository's index, verified with the key pinned when it was added."""
    from .trust import load_public, verify_index
    hit = _index_cache.get(repo.url)
    if hit and not force and time.time() - hit[0] < 600 and hit[2] == repo.key_id:
        return hit[1]
    try:
        doc = _get_index(repo.url)
        verify_index(doc, load_public(repo.public_key))
    except (InstallError, ValueError, TypeError) as exc:
        repo.error, repo.fetched_at = str(exc)[:500], utcnow()
        db.session.commit()
        raise InstallError(f"{repo.name}: {exc}") from exc
    repo.error, repo.fetched_at = "", utcnow()
    db.session.commit()
    _index_cache[repo.url] = (time.time(), doc, repo.key_id)
    return doc


def marketplace(fetch: bool = True) -> dict:
    """Everything that can be installed, one entry per plugin: what ships with labdex (installed
    or not, nothing to download) and what the enabled repositories offer. A repository entry for a
    plugin that ships with labdex is the same entry, with the repository's version."""
    from . import CAPABILITIES, catalog, updates
    reg = current_app.extensions["labdex"]["registry"]
    out: dict[str, dict] = {}
    errors = []
    for spec in reg.specs.values():
        if spec.builtin or spec.error:
            continue
        m = spec.manifest
        out[spec.id] = {
            "id": spec.id, "name": spec.name, "version": str(m.get("version", "")), "description": m.get("description", ""),
            "author": m.get("author", "labdex" if spec.bundled else ""), "tags": m.get("tags") or [], "capabilities": spec.capabilities,
            "kind": spec.kind, "requires": str(m.get("labdex") or ""), "compatible": True, "incompatible_reason": "",
            "source": "ships with labdex" if spec.bundled else spec.trust.get("detail") or "uploaded",
            "bundled": spec.bundled, "official": spec.bundled or spec.trust.get("status") == "official",
            "repository": "", "repository_id": None,
            "installed": catalog.is_installed(spec), "installed_version": str(m.get("version", "")) if catalog.is_installed(spec) else None,
            "update": False, "settings": [f.get("label") or f["key"] for f in spec.settings if not f["key"].startswith("schedule_")][:30],
            "schedules": sum(1 for f in spec.settings if f["key"].startswith("schedule_")),
            "actions": [a.get("label") or a["id"] for a in m.get("actions") or []][:30], "pages": [p.get("label") or p["id"] for p in spec.pages],
            "widgets": len(m.get("widgets") or []), "per_host": spec.per_host,
            "changes": changes_for((spec.path / "CHANGELOG.md").read_text(), str(m.get("version", ""))) if (spec.path / "CHANGELOG.md").is_file() else "",
        }
    repos = db.session.scalars(db.select(PluginRepository).where(PluginRepository.enabled.is_(True)).order_by(PluginRepository.name)) if fetch else []
    holds = updates.holds()
    for repo in repos:
        try:
            idx = fetch_repository(repo)
        except InstallError as exc:
            errors.append(f"{repo.name}: {exc}")
            continue
        for p in idx["plugins"]:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            pid = str(p["id"])
            cur = reg.get(pid)
            entry = out.get(pid) or {
                "id": pid, "name": p.get("name", pid), "description": p.get("description", ""), "author": p.get("author", ""),
                "tags": p.get("tags") or [], "capabilities": p.get("capabilities") or [], "kind": p.get("kind", "integration"),
                "bundled": False, "installed": False, "installed_version": None, "settings": [], "schedules": 0, "actions": [], "pages": [],
                "widgets": 0, "per_host": False, "source": repo.name}
            if entry.get("repository_id"):  # offered by an earlier repository already: the first one wins
                continue
            vers = versions_of(p)
            best = best_version(p)
            latest = vers[0] if vers else {"version": str(p.get("version", "")), "labdex": str(p.get("labdex") or ""), "yanked": "", "changes": ""}
            ok, why = compatibility({"labdex": latest["labdex"]} if latest["labdex"] else {})
            have = entry["installed_version"] or (str(cur.manifest.get("version", "")) if cur else "")
            target = best["version"] if best else ""
            installed = entry["installed"]
            mine = next((v for v in vers if v["version"] == have), None) if installed else None
            entry.update({
                "repository": repo.name, "repository_id": repo.id, "official": entry.get("official") or repo.official, "official_signed": bool(repo.official),
                "repo_version": target or latest["version"], "latest_version": latest["version"],
                "requires": str((best or latest).get("labdex") or entry.get("requires") or ""), "compatible": bool(best) or ok,
                "incompatible_reason": "" if best else why or ("every version is withdrawn" if vers and all(v["yanked"] for v in vers) else ""),
                "supported": bool(p.get("supported")), "source_url": str(p.get("source") or "") if str(p.get("source") or "").startswith("https://") else "",
                "versions": [{k: v[k] for k in ("version", "labdex", "changes", "yanked", "date")}
                             | {"compatible": compatibility({"labdex": v["labdex"]} if v["labdex"] else {})[0]} for v in vers],
                "withdrawn": (mine or {}).get("yanked", "") if installed else "",
                "update": bool(installed and have and target and _newer(target, have) and not (cur and cur.builtin) and pid not in holds),
                # what's new since the installed version, newest first
                "changes": "\n\n".join(f"{v['version']}: {v['changes']}" for v in vers if v["changes"] and (not have or _newer(v["version"], have)))[:4000]
                if installed else (latest.get("changes") or ""),
            })
            if not entry.get("version"):
                entry["version"] = target or latest["version"]
            out[pid] = entry
    for e in out.values():
        e["held"] = holds.get(e["id"], "")
        e["previous"] = previous_version(e["id"]) if e["installed"] else None  # kept to roll back to
        e.setdefault("versions", [])
        e.setdefault("withdrawn", "")
        e.setdefault("changes", "")
        e.setdefault("official_signed", False)
    return {"plugins": sorted(out.values(), key=lambda e: e["name"].lower()), "errors": errors, "capability_labels": CAPABILITIES}


OFFICIAL_SEEDED = "official_repo_seeded"
OFFICIAL_NAME = "labdex official"


def ensure_official_repository() -> None:
    """The official repository is there out of the box, once: remove it and it stays removed.
    Nothing is fetched here; the key is the one that ships with labdex."""
    from .. import naming, store
    from .trust import official_index_url, official_keys, public_b64
    url = official_index_url()
    keys = official_keys()
    if url and keys:  # the official repository follows the address and key this labdex ships with (a moved or re-keyed Marketplace)
        kid, (_, pub) = next(iter(keys.items()))
        for r in db.session.scalars(db.select(PluginRepository).where(PluginRepository.official.is_(True))):
            if (r.url, r.key_id) != (url, kid) and not db.session.scalar(db.select(PluginRepository).where(PluginRepository.url == url, PluginRepository.id != r.id)):
                r.url, r.key_id, r.public_key, r.error, r.fetched_at = url, kid, public_b64(pub), "", None
                _index_cache.clear()
        db.session.commit()
    if not url or naming.get_setting(OFFICIAL_SEEDED) == "true":
        return
    if keys:
        kid, (_, pub) = next(iter(keys.items()))
        with db.session.no_autoflush:
            # one added by hand already (by its address, or under the official name): that one becomes the official one
            row = (db.session.scalar(db.select(PluginRepository).where(PluginRepository.url == url))
                   or db.session.scalar(db.select(PluginRepository).where(PluginRepository.name == OFFICIAL_NAME)))
        if row is None:
            db.session.add(PluginRepository(name=OFFICIAL_NAME, url=url, key_id=kid, public_key=public_b64(pub), official=True,
                                            added_by="labdex"))
        else:
            row.url, row.key_id, row.public_key, row.official, row.error, row.fetched_at = url, kid, public_b64(pub), True, "", None
            _index_cache.clear()
    store.save_settings({OFFICIAL_SEEDED: "true"})
    db.session.commit()


def _newer(a: str, b: str) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return Version(a) > Version(b)
    except InvalidVersion:
        return a != b


def versions_of(entry: dict) -> list[dict]:
    """Every version a repository offers for one plugin, newest first. Old indexes list only
    the current one; newer ones also have `versions` (each with url, sha256, labdex, changes, yanked)."""
    out, seen = [], set()
    for v in [entry, *(entry.get("versions") or [])]:
        if not isinstance(v, dict) or not v.get("version") or str(v["version"]) in seen:
            continue
        seen.add(str(v["version"]))
        out.append({"version": str(v["version"]), "url": v.get("url", ""), "sha256": v.get("sha256", ""), "labdex": str(v.get("labdex") or ""),
                    "changes": str(v.get("changes") or "")[:4000], "yanked": str(v.get("yanked") or "")[:300], "date": str(v.get("date") or "")})
    return sorted(out, key=lambda v: _vkey(v["version"]), reverse=True)


def _vkey(v: str):
    from packaging.version import InvalidVersion, Version
    try:
        return (1, Version(v))
    except InvalidVersion:
        return (0, Version("0"))


def best_version(entry: dict) -> dict | None:
    """The newest version that isn't withdrawn and works with this labdex."""
    for v in versions_of(entry):
        if not v["yanked"] and compatibility({"labdex": v["labdex"]} if v["labdex"] else {})[0]:
            return v
    return None


def install_from_repository(repo: PluginRepository, pid: str, version: str | None = None) -> dict:
    from urllib.parse import urljoin
    idx = fetch_repository(repo)
    top = next((p for p in idx["plugins"] if isinstance(p, dict) and str(p.get("id")) == pid), None)
    if top is None:
        raise InstallError(f"'{pid}' isn't in {repo.name}")
    if version:
        entry = next((v for v in versions_of(top) if v["version"] == str(version)), None)
        if entry is None:
            raise InstallError(f"{repo.name} doesn't offer {pid} {version}")
    else:
        entry = best_version(top) or versions_of(top)[0]
    zurl = urljoin(repo.url, str(entry.get("url", "")))
    if not entry.get("sha256"):
        raise InstallError("the index has no checksum for this plugin")
    try:
        r = requests.get(zurl, timeout=60)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise InstallError(f"download failed: {exc}") from exc
    if hashlib.sha256(r.content).hexdigest() != entry["sha256"]:
        raise InstallError("download doesn't match the checksum in the index")
    info = stage(r.content, source=f"{repo.name}: {zurl}")
    if info["trust"].get("key_id") != repo.key_id or info["trust"]["status"] not in ("official", "community"):
        discard_stage(info["token"])
        raise InstallError(f"'{pid}' isn't signed with {repo.name}'s key")
    return info


def build_index_from_sources(sources: dict, out: Path, private_pem: bytes, name: str = "", description: str = "",
                             token: str = "", api: str = "https://api.github.com", get=None) -> dict:
    """For a Marketplace whose plugins live in their own git repositories (sources.yaml):

        name: labdex official
        description: …
        plugins:
          - repo: owner/labdex-plugin-opnsense        # GitHub: its releases are the versions
            withdrawn: {"1.2": "breaks on OPNsense 26.1"}
          - index: https://example.org/plugins/index.json   # or another signed index, taken as is
            key_id: 3f9a…                                     #   (pinned: only this key)

    Every release asset named <id>-<version>.zip is downloaded and checked: its checksum, its
    SIGNATURE.json (signed with this key, or the plugin is skipped) and that its manifest's id
    and version match the name. The index points at the assets where they are (nothing is
    copied), with the release notes as each version's changes. The index is signed with the key.
    A plugin signed with someone else's key belongs in that author's own repository (index),
    which people add next to this one: labdex pins one key per repository."""
    import tempfile
    from cryptography.hazmat.primitives import serialization
    from .trust import key_id, sign_index
    get = get or requests.get
    key = serialization.load_pem_private_key(private_pem, password=None)
    mine = {key_id(key.public_key()): ("this repository", key.public_key())}
    hdr = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    plugins, problems = [], []
    listed = list(sources.get("plugins") or [])
    for d in sources.get("discover") or []:  # every repository of an owner whose name starts with a prefix
        owner, prefix = str(d.get("owner") or ""), str(d.get("prefix") or "labdex-plugin-")
        try:
            found = []
            for page in range(1, 11):
                r = get(f"{api}/users/{owner}/repos?per_page=100&page={page}", headers=hdr, timeout=30)
                r.raise_for_status()
                batch = r.json()
                found += [x["full_name"] for x in batch if str(x.get("name", "")).startswith(prefix) and not x.get("archived") and not x.get("private")]
                if len(batch) < 100:
                    break
        except Exception as exc:
            problems.append(f"discovering {owner}/{prefix}*: {exc}")
            continue
        have = {str(x.get("repo") or "").lower() for x in listed}
        listed += [{"repo": f} for f in sorted(found) if f.lower() not in have]
    for src in listed:
        repo = str(src.get("repo") or "")
        withdrawn = {str(k): str(v) for k, v in (src.get("withdrawn") or {}).items()}
        try:
            rel = get(f"{api}/repos/{repo}/releases?per_page=100", headers=hdr, timeout=30)
            rel.raise_for_status()
            releases = [r for r in rel.json() if not r.get("draft") and not r.get("prerelease")]
        except Exception as exc:
            problems.append(f"{repo}: {exc}")
            continue
        versions, top = [], None
        for r in releases:
            for a in r.get("assets") or []:
                if not str(a.get("name", "")).endswith(".zip"):
                    continue
                try:
                    blob = get(a["browser_download_url"], timeout=60).content
                    with tempfile.TemporaryDirectory() as tmp:
                        pdir = _safe_extract(blob, Path(tmp) / "x")
                        m = yaml.safe_load((pdir / "plugin.yaml").read_text()) or {}
                        if a["name"] != f"{m.get('id')}-{m.get('version')}.zip":
                            raise InstallError(f"{a['name']} holds {m.get('id')} {m.get('version')}")
                        t = verify(pdir, m, mine)
                        if t["status"] not in ("official", "community") or t.get("key_id") not in mine:
                            raise InstallError(f"not signed with this repository's key ({t['detail']})")
                        problem = _validate(m, pdir)
                        if problem:
                            raise InstallError(problem)
                except Exception as exc:
                    problems.append(f"{repo} {a.get('name')}: {exc}")
                    continue
                ver = str(m["version"])
                v = {"version": ver, "url": a["browser_download_url"], "sha256": hashlib.sha256(blob).hexdigest(),
                     **({"labdex": str(m["labdex"])} if m.get("labdex") else {}),
                     "changes": str(r.get("body") or "").strip()[:4000], "date": str(r.get("published_at") or "")[:10],
                     **({"yanked": withdrawn[ver]} if ver in withdrawn else {})}
                versions.append(v)
                if top is None or _vkey(ver) > _vkey(top[0]["version"]):
                    top = (v, m)
        if top is None:
            problems.append(f"{repo}: no release with a signed plugin")
            continue
        v, m = top
        versions.sort(key=lambda x: _vkey(x["version"]), reverse=True)
        plugins.append({"id": m["id"], "name": m.get("name", m["id"]), "description": m.get("description", ""), "author": m.get("author", ""),
                        "tags": m.get("tags") or [], "capabilities": capabilities_of(m), "supported": bool(m.get("supported")),
                        "source": f"https://github.com/{repo}", **v, "versions": versions})
    out.mkdir(parents=True, exist_ok=True)
    doc = sign_index({"name": name or sources.get("name") or "plugins", "description": description or sources.get("description") or "",
                      "generated": datetime.now(UTC).isoformat(timespec="seconds"), "plugins": sorted(plugins, key=lambda p: p["id"])}, private_pem)
    (out / "index.json").write_text(json.dumps(doc, indent=2) + "\n")
    return {**doc, "problems": problems}


RELEASEIGNORE = ".releaseignore"
ALWAYS_LEFT_OUT = (".git", ".github", ".gitattributes", ".gitignore", RELEASEIGNORE, "__pycache__", "*.pyc", "*.zip")


def release_ignored(root: Path) -> list[Path]:
    """What a plugin's .releaseignore (and the usual repository files) keeps out of its release.

    One pattern per line, like .gitignore: `tests/` (a folder), `*.log` (any file named so,
    anywhere), `docs/draft.md` (a path from the plugin's root); `#` starts a comment."""
    from fnmatch import fnmatch
    pats = list(ALWAYS_LEFT_OUT)
    f = root / RELEASEIGNORE
    if f.is_file():
        pats += [ln.strip() for ln in f.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if any(rel == q or rel.startswith(q + "/") for q in (x.relative_to(root).as_posix() for x in out)):
            continue  # inside something already left out
        for pat in pats:
            folder = pat.endswith("/")
            pat = pat.strip("/")
            if folder and not p.is_dir():
                continue
            if fnmatch(rel, pat) if "/" in pat else fnmatch(p.name, pat):
                out.append(p)
                break
    return out


def prune_release(root: Path) -> list[str]:
    """Delete what release_ignored() names (on a copy made for the release), before signing."""
    gone = []
    for p in release_ignored(root):
        gone.append(p.relative_to(root).as_posix())
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    return gone


def changes_for(changelog: str, version: str) -> str:
    """The notes for one version from a plugin's CHANGELOG.md (a `## <version>` heading)."""
    import re
    m = re.search(rf"^##+\s*\[?v?{re.escape(version)}\]?\b[^\n]*\n(.*?)(?=^##+\s|\Z)", changelog, re.M | re.S)
    return m.group(1).strip()[:4000] if m else ""


def yank(out: Path, private_pem: bytes, pid: str, version: str, reason: str) -> dict:
    """For publishers: mark one version as withdrawn (or, with reason '', not any more) and re-sign the index."""
    from .trust import sign_index
    doc = json.loads((out / "index.json").read_text())
    entry = next((p for p in doc.get("plugins") or [] if str(p.get("id")) == pid), None)
    if entry is None:
        raise InstallError(f"no plugin '{pid}' in this index")
    hit = False
    for v in [entry, *(entry.get("versions") or [])]:
        if str(v.get("version")) == version:
            hit = True
            if reason:
                v["yanked"] = reason[:300]
            else:
                v.pop("yanked", None)
    if not hit:
        raise InstallError(f"the index has no {pid} {version}")
    for k in ("signature", "key_id", "public_key"):
        doc.pop(k, None)
    doc["generated"] = datetime.now(UTC).isoformat(timespec="seconds")
    doc = sign_index(doc, private_pem)
    (out / "index.json").write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def discard_stage(token: str) -> None:
    if token.isalnum():
        shutil.rmtree(_stage_root() / token, ignore_errors=True)


def build_repository(src: Path, out: Path, private_pem: bytes, name: str, description: str = "") -> dict:
    """For publishers: sign every plugin folder in src, zip them into out, write a signed index.json."""
    from .trust import sign, sign_index
    out.mkdir(parents=True, exist_ok=True)
    plugins = []
    old = {}  # what the index already had: older versions stay offered while their zip is still there
    if (out / "index.json").is_file():
        try:
            old = {str(p["id"]): p for p in json.loads((out / "index.json").read_text()).get("plugins") or [] if isinstance(p, dict) and p.get("id")}
        except (ValueError, KeyError):
            old = {}
    for d in sorted(p for p in src.iterdir() if (p / "plugin.yaml").is_file()):
        m = yaml.safe_load((d / "plugin.yaml").read_text()) or {}
        problem = _validate(m, d)
        if problem:
            raise InstallError(f"{d.name}: {problem}")
        sign(d, private_pem, name)
        zname = f"{m['id']}-{m.get('version', '0')}.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(d.rglob("*")):
                rel = f.relative_to(d)
                if f.is_file() and not any(part == "__pycache__" or part.endswith(".pyc") for part in rel.parts):
                    zf.write(f, f"{m['id']}/{rel.as_posix()}")
        (out / zname).write_bytes(buf.getvalue())
        ver = str(m.get("version", ""))
        log = (d / "CHANGELOG.md").read_text() if (d / "CHANGELOG.md").is_file() else ""
        prev = old.get(str(m["id"])) or {}
        history = [v for v in versions_of(prev) if v["version"] != ver and v["url"] and (out / v["url"]).is_file()] if prev else []
        for v in history:  # a version's notes may have been written after it was published
            v["changes"] = v["changes"] or changes_for(log, v["version"])
        date = next((v["date"] for v in versions_of(prev) if v["version"] == ver and v["date"]), "") if prev else ""
        current = {"version": ver, "url": zname, "sha256": hashlib.sha256(buf.getvalue()).hexdigest(),
                   **({"labdex": str(m["labdex"])} if m.get("labdex") else {}), "changes": changes_for(log, ver) or str(m.get("changes") or ""),
                   "date": date or datetime.now(UTC).date().isoformat()}
        plugins.append({"id": m["id"], "name": m.get("name", m["id"]), "description": m.get("description", ""), "author": m.get("author", ""),
                        "tags": m.get("tags") or [], "capabilities": capabilities_of(m), "supported": bool(m.get("supported")), **current,
                        "versions": [current, *[{k: v for k, v in h.items() if v or k in ("version", "url", "sha256")} for h in history]]})
    for pid, prev in old.items():  # a plugin whose folder is gone stays offered as it was (yank it to withdraw it)
        if not any(p["id"] == pid for p in plugins):
            plugins.append(prev)
    doc = sign_index({"name": name, "description": description, "generated": datetime.now(UTC).isoformat(timespec="seconds"),
                      "plugins": plugins}, private_pem)
    (out / "index.json").write_text(json.dumps(doc, indent=2) + "\n")
    return doc
