"""Git as the source of a site: its file (labdex/<site>.yaml) lives in a repository, and labdex keeps
the two in step.

* **Pull**: a new commit on the site's branch (seen on a webhook or a poll) is planned and imported.
* **Push**: an edit in labdex is written to the file and committed to the branch, or, with
  `mode: mr`, to a `labdex/<site>` branch with a merge request (GitLab) or pull request (GitHub).
* **Conflict**: both changed since the last sync. Nothing happens until someone picks a side
  (`sync(code, prefer="git" | "labdex")`).

Each site has a working copy in <data>/git/<site>. A token, when the repository needs one, is a
vault reference read for each run; it's passed to git in the environment, never written to disk.
`tick()` runs from the job worker every 30 seconds.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import subprocess
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import current_app

from . import audit, inventory, sites, store
from .models import Site, db, utcnow

MODES = ("commit", "mr")
URL_RE = re.compile(r"^(https://[^\s@]+|file://\S+|/\S+)$")  # no user:password@ in the URL: the token is a vault reference
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
TIMEOUT = 60
AUTHOR = ("labdex", "labdex@localhost")


class GitError(Exception):
    pass


# ------------------------------------------------------------------ settings
def config(site: Site) -> dict | None:
    return site.git if site.git and site.git.get("url") else None


def validate(code: str, data: dict | None, old: dict | None = None) -> dict | None:
    """The site's git settings from a form ({} or url '' turns it off). Raises store.ValidationError."""
    if not data or not str(data.get("url") or "").strip():
        return None
    errors = []
    url = str(data.get("url")).strip()
    if not URL_RE.match(url):
        errors.append({"field": "git.url", "message": "an https:// address (put a token in the vault, not in the address)"})
    branch = str(data.get("branch") or "main").strip()
    if not BRANCH_RE.match(branch) or ".." in branch:
        errors.append({"field": "git.branch", "message": "a branch name"})
    path = str(data.get("path") or f"labdex/{code}.yaml").strip().lstrip("/")
    if ".." in Path(path).parts or not path.endswith((".yaml", ".yml")):
        errors.append({"field": "git.path", "message": "a .yaml file inside the repository"})
    mode = str(data.get("mode") or "commit")
    if mode not in MODES:
        errors.append({"field": "git.mode", "message": "commit or mr"})
    try:
        poll = int(data.get("poll", 5))
        if not 0 <= poll <= 1440:
            raise ValueError
    except (TypeError, ValueError):
        errors.append({"field": "git.poll", "message": "minutes between checks, 0 to 1440 (0: webhook only)"})
        poll = 0
    token_ref = str(data.get("token_ref") or "").strip()
    if token_ref:
        from . import secretstore
        errors += secretstore.check_new([(old or {}).get("token_ref")] if (old or {}).get("token_ref") else [], [token_ref], "git.token_ref")
    if errors:
        raise store.ValidationError(errors)
    return {"url": url, "branch": branch, "path": path, "mode": mode, "poll": poll, "token_ref": token_ref,
            "hook_secret": (old or {}).get("hook_secret") or secrets.token_urlsafe(24)}


def public(site: Site) -> dict | None:
    """Settings and state for the UI: the hook secret only to those who may edit sites."""
    cfg = config(site)
    if cfg is None:
        return None
    st = site.git_state or {}
    return {**{k: v for k, v in cfg.items() if k != "hook_secret"},
            "hook_url": f"/api/git/hook/{site.code}",
            "state": {k: st.get(k) for k in ("commit", "synced_at", "error", "conflict", "mr_url", "pulled", "pushed")}}


# ------------------------------------------------------------------ git
def repo_dir(code: str) -> Path:
    return Path(current_app.config["LABDEX_DATA_DIR"]) / "git" / code


def _env(cfg: dict) -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "LC_ALL": "C"}
    ref = cfg.get("token_ref")
    if ref and cfg["url"].startswith("https://"):
        from . import secretstore
        try:
            token = secretstore.reveal(ref)
        except secretstore.RefError as exc:
            raise GitError(str(exc)) from None
        if not token:
            raise GitError(f"no secret {secretstore.label(ref)} in the vault")
        basic = base64.b64encode(f"oauth2:{token}".encode()).decode()
        # in the environment, not on the command line or in .git/config
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    return env


def _git(code: str, cfg: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    d = repo_dir(code)
    try:
        r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, env=_env(cfg), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} took longer than {TIMEOUT} s") from None
    if check and r.returncode != 0:
        raise GitError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:500]}")
    return r


def _prepare(code: str, cfg: dict) -> None:
    d = repo_dir(code)
    if not (d / ".git").exists():
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        _git(code, cfg, "init", "-q")
    _git(code, cfg, "remote", "remove", "origin", check=False)
    _git(code, cfg, "remote", "add", "origin", cfg["url"])


def fetch(code: str, cfg: dict) -> str:
    """The branch's head commit after fetching ('' when the branch doesn't exist yet)."""
    _prepare(code, cfg)
    r = _git(code, cfg, "fetch", "-q", "--depth", "50", "origin", f"+refs/heads/{cfg['branch']}:refs/remotes/origin/{cfg['branch']}", check=False)
    if r.returncode != 0:
        if "couldn't find remote ref" in (r.stderr or "").lower():
            return ""
        raise GitError(f"git fetch: {(r.stderr or r.stdout).strip()[:500]}")
    return _git(code, cfg, "rev-parse", f"refs/remotes/origin/{cfg['branch']}").stdout.strip()


def file_at(code: str, cfg: dict, commit: str) -> str | None:
    if not commit:
        return None
    r = _git(code, cfg, "show", f"{commit}:{cfg['path']}", check=False)
    return r.stdout if r.returncode == 0 else None


def _commit_file(code: str, cfg: dict, base: str, text: str, message: str) -> str:
    """A commit on top of `base` ('' = the first one) that sets the site's file; returns its sha."""
    if base:
        _git(code, cfg, "checkout", "-q", "--detach", "--force", base)
    else:
        _git(code, cfg, "checkout", "-q", "--orphan", f"labdex-{secrets.token_hex(4)}")
        _git(code, cfg, "rm", "-rq", "--cached", "--ignore-unmatch", ".")
    f = repo_dir(code) / cfg["path"]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(code, cfg, "add", "--", cfg["path"])
    _git(code, cfg, "-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}", "commit", "-q", "-m", message)
    return _git(code, cfg, "rev-parse", "HEAD").stdout.strip()


def _digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


# ------------------------------------------------------------------ sync
def _set_state(site: Site, **kw) -> dict:
    st = dict(site.git_state or {})
    st.update(kw)
    site.git_state = st
    return st


def sync(code: str, prefer: str | None = None, actor: tuple[str, str] = ("git", "system")) -> dict:
    """Bring labdex and the repository in step for one site. Returns what happened:
    {action: none | pulled | pushed | conflict | error, commit, plan?, error?}."""
    site = db.session.get(Site, code)
    cfg = config(site) if site else None
    if cfg is None:
        return {"action": "none"}
    st = dict(site.git_state or {})
    try:
        head = fetch(code, cfg)
        last = st.get("commit") or ""
        local = inventory.export_yaml(site=code)
        local_changed = _digest(local) != st.get("local")
        remote_text = file_at(code, cfg, head)
        remote_changed = head != last and remote_text != file_at(code, cfg, last)
        if not last:  # the first sync: whichever side has something
            used = sites.usage(code)
            empty_here = not used["hosts"] and not used["networks"]
            if remote_text == local:
                remote_changed = local_changed = False
            elif remote_text is None:  # nothing in git yet (or only in a merge request so far)
                remote_changed = False
            elif empty_here:
                remote_changed, local_changed = True, False
            else:
                remote_changed = local_changed = True
        if remote_changed and local_changed and prefer is None:
            msg = "both git and labdex changed this site since the last sync: choose which one to keep"
            _set_state(site, error=msg, conflict=True, checked_at=_now())
            db.session.commit()
            return {"action": "conflict", "commit": head, "error": msg}
        if remote_changed and prefer != "labdex":
            return _pull(site, cfg, head, remote_text, actor)
        if local_changed or prefer == "labdex":
            return _push(site, cfg, head, local, actor)
        _set_state(site, commit=head, local=_digest(local), error="", conflict=False, checked_at=_now())
        db.session.commit()
        return {"action": "none", "commit": head}
    except (GitError, OSError) as exc:
        db.session.rollback()
        site = db.session.get(Site, code)
        _set_state(site, error=str(exc)[:500], checked_at=_now())
        db.session.commit()
        return {"action": "error", "error": str(exc)}


def _now() -> str:
    return utcnow().isoformat(timespec="seconds") + "Z"


def _pull(site: Site, cfg: dict, head: str, text: str | None, actor) -> dict:
    from . import fileplan
    from .plugins import jobs
    code = site.code
    if text is None:
        raise GitError(f"{cfg['path']} isn't in {cfg['branch']} any more; labdex keeps the site as it is")
    p = fileplan.plan(text, site=code)
    if not p["ok"]:
        msg = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in p["errors"][:5])
        raise GitError(f"commit {head[:10]} can't be imported: {msg}")
    inventory.import_yaml(text, commit=False, site=code)
    s = p["summary"]
    audit.record("import", "inventory", code, None, {"commit": head, **s}, actor=actor,
                 summary=f"site {code} from git {head[:10]}: {s['create']} added, {s['update']} changed, {s['delete']} removed")
    site = db.session.get(Site, code)
    _set_state(site, commit=head, local=_digest(inventory.export_yaml(site=code)), error="", conflict=False,
               synced_at=_now(), checked_at=_now(), pulled=head)
    db.session.commit()
    jobs.emit("inventory.imported")
    return {"action": "pulled", "commit": head, "plan": p}


def _push(site: Site, cfg: dict, head: str, text: str, actor) -> dict:
    code = site.code
    message = f"labdex: site {code}"
    sha = _commit_file(code, cfg, head, text, message)
    if cfg["mode"] == "mr":
        branch = f"labdex/{code}"
        _git(code, cfg, "push", "-q", "--force", "origin", f"{sha}:refs/heads/{branch}")
        url = open_merge_request(cfg, branch, f"labdex: changes to site {code}")
        # the branch moves when the request is merged; then the pull brings it back in
        _set_state(site, local=_digest(text), error="", conflict=False, mr_url=url, checked_at=_now(), pushed=sha)
        result = {"action": "pushed", "commit": sha, "mr_url": url}
    else:
        _git(code, cfg, "push", "-q", "origin", f"{sha}:refs/heads/{cfg['branch']}")
        _set_state(site, commit=sha, local=_digest(text), error="", conflict=False, synced_at=_now(), checked_at=_now(), pushed=sha)
        result = {"action": "pushed", "commit": sha}
    audit.record("export", "inventory", code, None, {"commit": sha, "mode": cfg["mode"]}, actor=actor,
                 summary=f"site {code} to git {sha[:10]}" + (" (merge request)" if cfg["mode"] == "mr" else ""))
    db.session.commit()
    return result


# ------------------------------------------------------------------ merge requests
def _forge(url: str) -> tuple[str, str, str]:
    """(kind, api base, project path) for an https URL on GitLab or GitHub."""
    u = urlparse(url)
    project = u.path.strip("/").removesuffix(".git")
    if u.hostname == "github.com":
        return "github", "https://api.github.com", project
    return "gitlab", f"{u.scheme}://{u.netloc}/api/v4", project


def open_merge_request(cfg: dict, branch: str, title: str, session=None, token: str | None = None, body: str = "Changes made in labdex.") -> str:
    """Opens (or finds the open) merge request from `branch` to cfg's branch; returns its URL.
    The token comes from the vault reference in cfg, or is given (a plugin's own secret)."""
    import requests
    if not cfg["url"].startswith("https://"):
        return ""
    from . import secretstore
    if token is None:
        token = secretstore.reveal(cfg["token_ref"]) if cfg.get("token_ref") else ""
    if not token:
        raise GitError("opening a merge request needs a token (an API token in the vault)")
    kind, api, project = _forge(cfg["url"])
    s = session or requests
    if kind == "github":
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        owner = project.split("/")[0]
        r = s.get(f"{api}/repos/{project}/pulls", params={"head": f"{owner}:{branch}", "state": "open"}, headers=h, timeout=30)
        if r.status_code == 200 and r.json():
            return r.json()[0]["html_url"]
        r = s.post(f"{api}/repos/{project}/pulls", json={"title": title, "head": branch, "base": cfg["branch"],
                                                          "body": body}, headers=h, timeout=30)
        if r.status_code >= 300:
            raise GitError(f"GitHub refused the pull request: HTTP {r.status_code} {r.text[:200]}")
        return r.json()["html_url"]
    h = {"PRIVATE-TOKEN": token}
    pid = quote(project, safe="")
    r = s.get(f"{api}/projects/{pid}/merge_requests", params={"source_branch": branch, "state": "opened"}, headers=h, timeout=30)
    if r.status_code == 200 and r.json():
        return r.json()[0]["web_url"]
    r = s.post(f"{api}/projects/{pid}/merge_requests", json={"source_branch": branch, "target_branch": cfg["branch"], "title": title,
                                                             "description": body, "remove_source_branch": True},
               headers=h, timeout=30)
    if r.status_code >= 300:
        raise GitError(f"GitLab refused the merge request: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["web_url"]


# ------------------------------------------------------------------ webhook and timer
def hook_ok(site: Site, headers, body: bytes) -> bool:
    """A GitLab (X-Gitlab-Token) or GitHub (X-Hub-Signature-256) webhook with the site's secret."""
    cfg = config(site)
    if cfg is None:
        return False
    secret = cfg.get("hook_secret") or ""
    if headers.get("X-Gitlab-Token"):
        return hmac.compare_digest(headers["X-Gitlab-Token"], secret)
    sig = headers.get("X-Hub-Signature-256", "")
    if sig.startswith("sha256="):
        want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig[7:], want)
    return False


def request_pull(site: Site) -> None:
    _set_state(site, pull=True)
    db.session.commit()


def due(site: Site, now=None) -> bool:
    """Whether the timer wants a sync: a webhook asked, labdex changed, or the poll interval passed."""
    cfg = config(site)
    if cfg is None:
        return False
    st = site.git_state or {}
    if st.get("conflict"):
        return False  # until someone chooses
    if st.get("pull"):
        return True
    if _digest(inventory.export_yaml(site=site.code)) != st.get("local"):
        return True
    if not cfg.get("poll"):
        return False
    checked = st.get("checked_at")
    if not checked:
        return True
    from datetime import datetime
    last = datetime.fromisoformat(checked.rstrip("Z"))
    return (now or utcnow()) - last >= timedelta(minutes=cfg["poll"])


def tick() -> list[dict]:
    """One round for every site with a git source (from the job worker)."""
    out = []
    for site in db.session.scalars(db.select(Site).where(Site.git.is_not(None))).all():
        try:
            if due(site):
                code = site.code
                _set_state(site, pull=False)
                db.session.commit()
                out.append({"site": code, **sync(code)})
        except Exception:  # one site's trouble never stops the others
            db.session.rollback()
            current_app.logger.exception("git sync of site %s failed", site.code)
    return out
