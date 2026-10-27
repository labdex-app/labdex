"""About: version, schema, database, plugins and the changelog; optional release check."""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from flask import current_app, g, jsonify

from .. import naming, schema
from ..models import Host, Integration, db
from . import bp

_release_cache: dict = {}


def changelog_text() -> str:
    here = Path(__file__).resolve().parent.parent
    for p in (here / "CHANGELOG.md", here.parents[1] / "CHANGELOG.md"):  # packaged copy, or the repository's
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def latest_release(repo: str) -> dict | None:
    """GitHub's latest release of owner/repo, cached for six hours. None when unreachable."""
    hit = _release_cache.get(repo)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    import requests
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=5,
                         headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        d = r.json()
        out = {"tag": d.get("tag_name", ""), "name": d.get("name", ""), "url": d.get("html_url", ""), "published": d.get("published_at", "")}
    except Exception:
        out = None
    _release_cache[repo] = (time.time(), out)
    return out


def _newer(tag: str, mine: str) -> bool:
    def parts(v):
        return tuple(int(x) for x in v.lstrip("v").split("-")[0].split(".") if x.isdigit())
    try:
        return parts(tag) > parts(mine)
    except ValueError:
        return False


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
    repo = naming.get_setting("update_check_repo").strip()
    if naming.get_setting("update_check") == "true" and repo:
        rel = latest_release(repo)
        out["update_check"] = {"repo": repo, "latest": rel, "newer": bool(rel and _newer(rel["tag"], version))}
    return jsonify(out)
