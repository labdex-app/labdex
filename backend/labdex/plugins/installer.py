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
        backup = dest.with_name(f".{info['id']}.previous")
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
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
    return {**info, "installed": True}


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
    from . import CAPABILITIES, catalog
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
        }
    repos = db.session.scalars(db.select(PluginRepository).where(PluginRepository.enabled.is_(True)).order_by(PluginRepository.name)) if fetch else []
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
            ok, why = compatibility(p)
            cur = reg.get(pid)
            entry = out.get(pid) or {
                "id": pid, "name": p.get("name", pid), "description": p.get("description", ""), "author": p.get("author", ""),
                "tags": p.get("tags") or [], "capabilities": p.get("capabilities") or [], "kind": p.get("kind", "integration"),
                "bundled": False, "installed": False, "installed_version": None, "settings": [], "schedules": 0, "actions": [], "pages": [],
                "widgets": 0, "per_host": False, "source": repo.name}
            newer = str(p.get("version", ""))
            installed = entry["installed"]
            have = entry["installed_version"] or (str(cur.manifest.get("version", "")) if cur else "")
            entry.update({
                "repository": repo.name, "repository_id": repo.id, "official": entry.get("official") or repo.official,
                "repo_version": newer, "requires": str(p.get("labdex") or entry.get("requires") or ""), "compatible": ok, "incompatible_reason": why,
                "supported": bool(p.get("supported")),
                "update": bool(installed and have and _newer(newer, have) and not (cur and cur.builtin)),
            })
            entry.setdefault("version", newer)
            if not entry.get("version"):
                entry["version"] = newer
            out[pid] = entry
    return {"plugins": sorted(out.values(), key=lambda e: e["name"].lower()), "errors": errors, "capability_labels": CAPABILITIES}


OFFICIAL_SEEDED = "official_repo_seeded"


def ensure_official_repository() -> None:
    """The official repository is there out of the box, once: remove it and it stays removed.
    Nothing is fetched here; the key is the one that ships with labdex."""
    from .. import naming, store
    from .trust import official_index_url, official_keys, public_b64
    url = official_index_url()
    if not url or naming.get_setting(OFFICIAL_SEEDED) == "true":
        return
    keys = official_keys()
    if keys and db.session.scalar(db.select(PluginRepository).where(PluginRepository.url == url)) is None:
        kid, (_, pub) = next(iter(keys.items()))
        db.session.add(PluginRepository(name="labdex official", url=url, key_id=kid, public_key=public_b64(pub), official=True,
                                        added_by="labdex"))
    store.save_settings({OFFICIAL_SEEDED: "true"})
    db.session.commit()


def _newer(a: str, b: str) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return Version(a) > Version(b)
    except InvalidVersion:
        return a != b


def install_from_repository(repo: PluginRepository, pid: str) -> dict:
    from urllib.parse import urljoin
    idx = fetch_repository(repo)
    entry = next((p for p in idx["plugins"] if isinstance(p, dict) and str(p.get("id")) == pid), None)
    if entry is None:
        raise InstallError(f"'{pid}' isn't in {repo.name}")
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


def discard_stage(token: str) -> None:
    if token.isalnum():
        shutil.rmtree(_stage_root() / token, ignore_errors=True)


def build_repository(src: Path, out: Path, private_pem: bytes, name: str, description: str = "") -> dict:
    """For publishers: sign every plugin folder in src, zip them into out, write a signed index.json."""
    from .trust import sign, sign_index
    out.mkdir(parents=True, exist_ok=True)
    plugins = []
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
        plugins.append({"id": m["id"], "name": m.get("name", m["id"]), "version": str(m.get("version", "")),
                        "description": m.get("description", ""), "author": m.get("author", ""), "tags": m.get("tags") or [],
                        "capabilities": capabilities_of(m), "supported": bool(m.get("supported")),
                        **({"labdex": str(m["labdex"])} if m.get("labdex") else {}),
                        "url": zname, "sha256": hashlib.sha256(buf.getvalue()).hexdigest()})
    doc = sign_index({"name": name, "description": description, "generated": datetime.now(UTC).isoformat(timespec="seconds"),
                      "plugins": plugins}, private_pem)
    (out / "index.json").write_text(json.dumps(doc, indent=2) + "\n")
    return doc
