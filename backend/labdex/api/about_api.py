"""About: version, schema, database, plugins and the changelog; optional release check."""
from __future__ import annotations

import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g, jsonify

from .. import naming, schema
from ..models import Host, Integration, db
from . import bp, err

_release_cache: dict = {}


def changelog_text() -> str:
    here = Path(__file__).resolve().parent.parent
    for p in (here / "CHANGELOG.md", here.parents[1] / "CHANGELOG.md"):  # packaged copy, or the repository's
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


DEFAULT_PROJECT = "https://github.com/labdex-app/labdex"


def project_url() -> str:
    """Where labdex lives (the setting), as https://host/owner/repo. An owner/repo from before 1.24 is GitHub."""
    url = naming.get_setting("project_url").strip().rstrip("/").removesuffix(".git")
    old = naming.get_setting("update_check_repo").strip()
    if old and (not url or url == DEFAULT_PROJECT):
        url = old if "://" in old else f"https://github.com/{old}"
    return url or DEFAULT_PROJECT


def release_api(url: str) -> list[tuple[str, str]]:
    """Where to ask for the latest release, by platform: [(kind, api url)], most likely first.
    github.com → api.github.com; a host named gitlab… → GitLab; anything else is tried as
    Gitea/Forgejo (Codeberg …), then GitLab, then GitHub Enterprise."""
    from urllib.parse import quote, urlsplit
    u = urlsplit(url)
    path = u.path.strip("/")
    if not u.scheme or path.count("/") < 1:
        raise ValueError("like https://github.com/owner/repo")
    base = f"{u.scheme}://{u.netloc}"
    gh = ("github", f"https://api.github.com/repos/{path}/releases/latest" if u.netloc in ("github.com", "www.github.com")
          else f"{base}/api/v3/repos/{path}/releases/latest")
    gl = ("gitlab", f"{base}/api/v4/projects/{quote(path, safe='')}/releases/permalink/latest")
    gt = ("gitea", f"{base}/api/v1/repos/{path}/releases/latest")
    if u.netloc in ("github.com", "www.github.com"):
        return [gh]
    if "gitlab" in u.netloc:
        return [gl, gt]
    return [gt, gl, gh]


RELEASE_TTL = 3600  # a found release is kept an hour, a failure ten minutes
FORCE_FLOOR = 30  # "Check now" asks again at most this often (unauthenticated GitHub allows 60 an hour)


def latest_release(url: str, force: bool = False) -> dict:
    """The project's latest release: {tag, name, url, published, checked} or {error, checked}.
    Cached (see RELEASE_TTL); force asks again unless the last answer is under FORCE_FLOOR seconds
    old. Only a GET to the platform's public API; nothing is sent."""
    hit = _release_cache.get(url)
    if hit and time.time() - hit[0] < (FORCE_FLOOR if force else RELEASE_TTL if "tag" in hit[1] else 600):
        return hit[1]
    import requests
    out: dict = {}
    try:
        tries = release_api(url)
    except ValueError as exc:
        tries, out = [], {"error": f"not a project address ({exc})"}
    for kind, api in tries:
        try:
            r = requests.get(api, timeout=5, headers={"Accept": "application/json"})
            if r.status_code == 404:
                out = {"error": "no release published yet" if kind == tries[0][0] else out.get("error", "no release published yet")}
                continue
            r.raise_for_status()
            d = r.json()
            if kind == "gitlab":
                out = {"tag": d.get("tag_name", ""), "name": d.get("name", ""), "url": (d.get("_links") or {}).get("self") or f"{url}/-/releases",
                       "published": d.get("released_at", "")}
            else:
                out = {"tag": d.get("tag_name", ""), "name": d.get("name", ""), "url": d.get("html_url", "") or f"{url}/releases",
                       "published": d.get("published_at", "")}
            out["platform"] = kind
            break
        except requests.RequestException as exc:
            out = {"error": f"can't reach {api.split('/')[2]} ({type(exc).__name__})"}
        except ValueError:
            out = {"error": "the answer wasn't a release"}
    now = time.time()
    out = {**(out or {"error": "no answer"}), "checked": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")}
    _release_cache[url] = (now, out)
    return out


def _newer(tag: str, mine: str) -> bool:
    """Is release tag (v1.25.0) newer than this version (1.24.1.post1)? PEP 440, so post, dev and
    rc versions order right; a tag that isn't a version falls back to comparing its numbers."""
    from packaging.version import InvalidVersion, Version
    try:
        return Version(tag) > Version(mine)
    except InvalidVersion:
        pass

    def parts(v):
        return tuple(int(m.group()) for x in v.lstrip("vV").split("-")[0].split(".") if (m := re.match(r"\d+", x)))
    return parts(tag) > parts(mine)


def update_check(force: bool = False) -> dict | None:
    """The About page's Updates card, or None when the check is off in Settings."""
    if naming.get_setting("update_check") != "true":
        return None
    project = project_url()
    rel = latest_release(project, force=force)
    ok = "tag" in rel
    return {"project": project, "latest": rel if ok else None, "error": rel.get("error", ""), "checked": rel.get("checked", ""),
            "newer": bool(ok and _newer(rel["tag"], current_app.config["LABDEX_VERSION"]))}


def _jobs_status() -> dict:
    from ..plugins import jobs
    q = jobs.queue_status()
    if not g.principal.is_admin:
        q["workers"] = [{"mode": w["mode"], "running": w["running"]} for w in q["workers"]]
    return q


@bp.get("/about")
def about():
    version = current_app.config["LABDEX_VERSION"]
    with db.engine.connect() as conn:
        rev = schema.current(conn)
    url = db.engine.url
    reg = current_app.extensions["labdex"]["registry"]
    out = {
        "version": version,
        "schema": {"current": rev, "newest": schema.head()},
        "database": {"backend": url.get_backend_name(),
                     # where it lives is for administrators only
                     "where": (url.database if url.get_backend_name() == "sqlite" else (url.host or "")) if g.principal.is_admin else ""},
        "python": sys.version.split()[0], "platform": platform.platform(terse=True),
        "counts": {"hosts": db.session.scalar(db.select(db.func.count()).select_from(Host)),
                   "integrations": db.session.scalar(db.select(db.func.count()).select_from(Integration))},
        "plugins": [{"id": s.id, "name": s.name, "version": str(s.manifest.get("version", "")), "kind": s.kind,
                     "trust": s.trust.get("status"), "builtin": s.builtin, "error": s.error}
                    for s in sorted(reg.specs.values(), key=lambda s: (not s.builtin, s.id))],
        "changelog": changelog_text(),
        "update_check": None,
        "jobs": _jobs_status(),
    }
    out["project_url"] = project_url()
    out["update_check"] = update_check()
    return jsonify(out)


@bp.post("/about/update-check")
def check_for_update():
    """Check now: ask the project's git platform for its latest release, past the cache."""
    if naming.get_setting("update_check") != "true":
        return err(409, "the release check is off (Settings)")
    return jsonify(update_check(force=True))


@bp.get("/openapi.json")
def openapi_spec():
    """The OpenAPI 3 description of this API, built from its routes."""
    from .. import openapi
    return jsonify(openapi.build(current_app))
