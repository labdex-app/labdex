"""Git as the source of a site: labdex pulls new commits and pushes its own edits."""
import subprocess

import pytest
import yaml

from labdex import gitsource
from labdex.models import AuditEvent, Host, Site, db


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "infra.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return bare


def clone(remote, tmp_path, name="work"):
    d = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(remote), str(d)], check=True, capture_output=True)
    return d


def connect(admin, remote, **extra):
    r = admin.put("/api/sites/home", json={"name": "Home", "git": {"url": str(remote), "branch": "main", **extra}})
    assert r.status_code == 200, r.get_json()
    return next(s for s in r.get_json()["sites"] if s["code"] == "home")["git"]


def host_ip(name="srv-vm-dns-01"):
    return db.session.scalar(db.select(Host).where(Host.name == name)).interfaces[0].ip4


def test_settings_are_checked(admin, remote):
    bad = admin.put("/api/sites/home", json={"git": {"url": "https://me:secret@gitlab.example.com/x.git"}})
    assert bad.status_code == 422 and "git.url" in str(bad.get_json())
    assert admin.put("/api/sites/home", json={"git": {"url": str(remote), "path": "../etc/x.yaml"}}).status_code == 422
    g = connect(admin, remote)
    assert g["path"] == "labdex/home.yaml" and g["mode"] == "commit" and g["hook_secret"]
    # an empty address turns it off
    assert admin.put("/api/sites/home", json={"git": {}}).status_code == 200
    assert db.session.get(Site, "home").git is None


def test_first_sync_pushes_then_edits_follow(admin, remote, tmp_path):
    connect(admin, remote)
    out = gitsource.sync("home")
    assert out["action"] == "pushed"
    work = clone(remote, tmp_path)
    data = yaml.safe_load((work / "labdex" / "home.yaml").read_text())
    assert data["site"] == "home" and any(h["name"] == "srv-vm-dns-01" for h in data["hosts"])
    assert gitsource.sync("home")["action"] == "none"
    assert not gitsource.due(db.session.get(Site, "home")) or db.session.get(Site, "home").git["poll"]
    # an edit in labdex is due and goes out as a commit
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    h["function"] = "DNS (edited in labdex)"
    assert admin.put("/api/hosts/srv-vm-dns-01", json=h).status_code == 200
    assert gitsource.due(db.session.get(Site, "home"))
    assert [r["action"] for r in gitsource.tick()] == ["pushed"]
    git(work, "pull", "-q")
    assert "DNS (edited in labdex)" in (work / "labdex" / "home.yaml").read_text()
    assert git(work, "log", "-1", "--format=%an %s") == "labdex labdex: site home"


def test_a_commit_comes_in_through_the_webhook(admin, remote, tmp_path):
    g = connect(admin, remote)
    gitsource.sync("home")
    work = clone(remote, tmp_path)
    f = work / "labdex" / "home.yaml"
    data = yaml.safe_load(f.read_text())
    host = next(h for h in data["hosts"] if h["name"] == "srv-vm-dns-01")
    host["interfaces"][0]["ip4"] = "10.10.100.199"
    f.write_text(yaml.safe_dump(data, sort_keys=False))
    git(work, "commit", "-qam", "move the resolver")
    git(work, "push", "-q")
    hook = admin.application.test_client()
    assert hook.post("/api/git/hook/home", headers={"X-Gitlab-Token": "wrong"}).status_code == 403
    r = hook.post("/api/git/hook/home", headers={"X-Gitlab-Token": g["hook_secret"]})
    assert r.status_code == 202
    db.session.expire_all()
    assert host_ip() == "10.10.100.199"
    ev = db.session.scalars(db.select(AuditEvent).order_by(AuditEvent.id.desc())).first()
    assert "from git" in ev.summary and ev.actor == "git"
    assert gitsource.sync("home")["action"] == "none"  # what came in isn't pushed back


def test_github_signature(admin, remote):
    import hashlib
    import hmac
    g = connect(admin, remote)
    body = b'{"ref": "refs/heads/main"}'
    sig = "sha256=" + hmac.new(g["hook_secret"].encode(), body, hashlib.sha256).hexdigest()
    hook = admin.application.test_client()
    assert hook.post("/api/git/hook/home", data=body, headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 403
    assert hook.post("/api/git/hook/home", data=body, headers={"X-Hub-Signature-256": sig}).status_code == 202


def test_a_bad_commit_changes_nothing(admin, remote, tmp_path):
    connect(admin, remote)
    gitsource.sync("home")
    work = clone(remote, tmp_path)
    f = work / "labdex" / "home.yaml"
    data = yaml.safe_load(f.read_text())
    data["hosts"].append({"name": "Bad Name", "zone": "SRV"})
    f.write_text(yaml.safe_dump(data))
    git(work, "commit", "-qam", "oops")
    git(work, "push", "-q")
    before = host_ip()
    out = gitsource.sync("home")
    assert out["action"] == "error" and "can't be imported" in out["error"]
    assert host_ip() == before
    assert "can't be imported" in db.session.get(Site, "home").git_state["error"]


def test_conflict_until_someone_chooses(admin, remote, tmp_path):
    connect(admin, remote)
    gitsource.sync("home")
    work = clone(remote, tmp_path)
    f = work / "labdex" / "home.yaml"
    data = yaml.safe_load(f.read_text())
    next(h for h in data["hosts"] if h["name"] == "srv-vm-dns-01")["function"] = "from git"
    f.write_text(yaml.safe_dump(data))
    git(work, "commit", "-qam", "edit in git")
    git(work, "push", "-q")
    h = admin.get("/api/hosts/srv-vm-dns-01").get_json()
    h["function"] = "from labdex"
    admin.put("/api/hosts/srv-vm-dns-01", json=h)
    r = admin.post("/api/sites/home/git/sync", json={})
    assert r.status_code == 409 and r.get_json()["action"] == "conflict"
    assert not gitsource.due(db.session.get(Site, "home"))  # the timer waits for a choice
    r = admin.post("/api/sites/home/git/sync", json={"prefer": "git"})
    assert r.status_code == 200 and r.get_json()["action"] == "pulled" and "from git" in r.get_json()["plan_markdown"]
    assert admin.get("/api/hosts/srv-vm-dns-01").get_json()["function"] == "from git"


def test_merge_request_mode_pushes_a_branch(admin, remote, tmp_path):
    connect(admin, remote, mode="mr")
    gitsource.sync("home")  # the first file goes to labdex/home, not main
    branches = subprocess.run(["git", "-C", str(remote), "branch", "--format=%(refname:short)"], capture_output=True, text=True).stdout.split()
    assert branches == ["labdex/home"]
    assert gitsource.sync("home")["action"] == "none"


class FakeForge:
    def __init__(self, existing=None):
        self.calls = []
        self.existing = existing or []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw.get("params")))
        return Resp(200, self.existing)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw.get("json")))
        return Resp(201, {"web_url": "https://gitlab.example.com/infra/-/merge_requests/7", "html_url": "https://github.com/o/r/pull/7"})


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, ""

    def json(self):
        return self._data


def test_open_merge_request(app, monkeypatch):
    from labdex import secretstore
    monkeypatch.setattr(secretstore, "reveal", lambda ref: "glpat-example")
    cfg = {"url": "https://gitlab.example.com/infra/bitsandbricks.git", "branch": "main", "token_ref": "vault:git/token"}
    forge = FakeForge()
    url = gitsource.open_merge_request(cfg, "labdex/home", "labdex: changes", session=forge)
    assert url.endswith("/merge_requests/7")
    assert forge.calls[1][1] == "https://gitlab.example.com/api/v4/projects/infra%2Fbitsandbricks/merge_requests"
    assert forge.calls[1][2]["source_branch"] == "labdex/home" and forge.calls[1][2]["target_branch"] == "main"
    # an open one is reused
    forge = FakeForge(existing=[{"web_url": "https://gitlab.example.com/infra/-/merge_requests/3"}])
    assert gitsource.open_merge_request(cfg, "labdex/home", "t", session=forge).endswith("/3") and len(forge.calls) == 1
    gh = FakeForge()
    cfg["url"] = "https://github.com/o/r.git"
    assert gitsource.open_merge_request(cfg, "labdex/home", "t", session=gh).endswith("/pull/7")
    assert gh.calls[1][1] == "https://api.github.com/repos/o/r/pulls"


def test_token_goes_in_the_environment(app, monkeypatch):
    from labdex import secretstore
    monkeypatch.setattr(secretstore, "reveal", lambda ref: "glpat-example")
    env = gitsource._env({"url": "https://gitlab.example.com/x.git", "token_ref": "vault:git/token"})
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader" and env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert "glpat-example" not in env["GIT_CONFIG_VALUE_0"]  # base64, and never on a command line
