"""Flux files as code: apps → Flux manifests, planned against a git repository, proposed on a
labdex branch. The live branch is never written."""
import subprocess

import pytest
import yaml

from labdex import fluxfiles
from labdex.models import Job, db

from .test_plugins import make_instance, run

APPS = """
- name: grafana
  namespace: monitoring
  helm: {repo: https://grafana.github.io/helm-charts, chart: grafana, version: "8.x"}
  values: {adminUser: admin}
  depends_on: [infra]
- name: infra
  path: apps/infra
"""


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ------------------------------------------------------------------ rendering
def test_render_and_validate():
    apps = fluxfiles.parse(APPS)
    files = fluxfiles.render(apps, "test")
    assert sorted(files) == ["grafana.yaml", "infra.yaml", "kustomization.yaml"]
    assert all(t.startswith(fluxfiles.MARK) for t in files.values())
    repo, release = [d for d in yaml.safe_load_all(files["grafana.yaml"]) if d]
    assert repo["kind"] == "HelmRepository" and repo["spec"]["url"] == "https://grafana.github.io/helm-charts"
    assert release["apiVersion"] == "helm.toolkit.fluxcd.io/v2" and release["spec"]["targetNamespace"] == "monitoring"
    assert release["spec"]["chart"]["spec"] == {"chart": "grafana", "version": "8.x",
                                                "sourceRef": {"kind": "HelmRepository", "name": "grafana", "namespace": "flux-system"}}
    assert release["spec"]["dependsOn"] == [{"name": "infra", "namespace": "flux-system"}] and release["spec"]["values"] == {"adminUser": "admin"}
    (ks,) = [d for d in yaml.safe_load_all(files["infra.yaml"]) if d]
    assert ks["kind"] == "Kustomization" and ks["spec"]["path"] == "./apps/infra" and ks["spec"]["sourceRef"]["name"] == "flux-system"
    assert yaml.safe_load(files["kustomization.yaml"].split("\n", 1)[1])["resources"] == ["grafana.yaml", "infra.yaml"]

    with pytest.raises(fluxfiles.AppError) as e:
        fluxfiles.parse("- {name: Bad_Name, path: ../x}\n- {name: b, helm: {repo: http://x, chart: c}, depends_on: [nope]}\n- {name: c}")
    fields = {x["field"] for x in e.value.errors}
    assert {"apps[0].name", "apps[0].path", "apps[1].helm.repo", "apps[1].depends_on", "apps[2]"} <= fields


def test_diff_only_touches_labdex_files():
    want = fluxfiles.render(fluxfiles.parse("- {name: infra, path: ./apps/infra}"), "t")
    have = {"old.yaml": fluxfiles.MARK + " (t)\nold\n", "hand.yaml": "kind: ConfigMap\n", "infra.yaml": want["infra.yaml"]}
    ops = {d["file"]: d["op"] for d in fluxfiles.diff(want, have)}
    assert ops == {"old.yaml": "delete", "kustomization.yaml": "create"}  # hand.yaml stays, infra.yaml is the same
    ops = fluxfiles.diff(want, {"infra.yaml": "kind: Kustomization\n"})
    assert ops[0]["op"] == "conflict" and ops[0]["file"] == "infra.yaml"


# ------------------------------------------------------------------ through the plugin
@pytest.fixture
def fleet(tmp_path):
    bare = tmp_path / "fleet.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    (work / "clusters" / "oak" / "labdex").mkdir(parents=True)
    (work / "clusters" / "oak" / "labdex" / "notes.yaml").write_text("# written by hand\nkind: ConfigMap\n")
    (work / "clusters" / "oak" / "flux-system.yaml").write_text("kind: Kustomization\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "fleet")
    git(work, "push", "-q", "origin", "main")
    return bare, work


def test_plan_needs_an_approval_and_pushes_only_a_labdex_branch(admin, fleet):
    bare, work = fleet
    main_before = git(bare, "rev-parse", "main")
    make_instance(admin, "kubernetes", "oak", {"url": "https://k8s.example.net:6443", "token": "kt", "cluster": "oak",
                                               "flux_repo": str(bare), "apps": APPS})
    preview = run(admin, "oak", "flux_preview")
    assert admin.get(f"/api/jobs/{preview['id']}").get_json()["result"]["files"] == ["grafana.yaml", "infra.yaml", "kustomization.yaml"]
    admin.post("/api/approval-policies", json={"name": "no approvals", "approvals": 0})
    job = run(admin, "oak", "flux_files")
    job = admin.get(f"/api/jobs/{job['id']}").get_json()
    assert job["status"] == "planned" and job["needed"] == 1, job                     # a person says yes
    assert sorted((c["op"], c["name"]) for c in job["plan"]) == [
        ("create", "clusters/oak/labdex/grafana.yaml"), ("create", "clusters/oak/labdex/infra.yaml"),
        ("create", "clusters/oak/labdex/kustomization.yaml")]
    assert "+  path: ./apps/infra" in next(c for c in job["plan"] if c["name"].endswith("infra.yaml"))["after"]["diff"]
    assert git(bare, "branch", "--format=%(refname:short)").split() == ["main"]        # planning wrote nothing

    done = admin.post(f"/api/jobs/{job['id']}/approve", json={"comment": "ok"}).get_json()
    done = admin.get(f"/api/jobs/{job['id']}").get_json()
    assert done["status"] == "applied", done
    assert done["result"]["branch"] == "labdex/flux-oak" and done["result"]["merge_request"] == ""
    assert git(bare, "rev-parse", "main") == main_before                                # the live branch is untouched
    files = git(bare, "ls-tree", "--name-only", "labdex/flux-oak:clusters/oak/labdex").split()
    assert files == ["grafana.yaml", "infra.yaml", "kustomization.yaml", "notes.yaml"]  # the hand-written file stays
    assert git(bare, "log", "-1", "--format=%an %s", "labdex/flux-oak") == "labdex labdex: Flux apps of cluster oak"


def test_a_hand_written_file_of_the_same_name_stops_the_plan(admin, fleet):
    bare, work = fleet
    make_instance(admin, "kubernetes", "oak", {"url": "https://k8s.example.net:6443", "token": "kt", "cluster": "oak",
                                               "flux_repo": str(bare), "apps": "- {name: notes, path: ./apps/notes}"})
    job = admin.get(f"/api/jobs/{run(admin, 'oak', 'flux_files')['id']}").get_json()
    assert job["status"] == "failed" and "weren't written by labdex" in job["result"]["error"]


def test_settings_are_checked(admin, fleet):
    make_instance(admin, "kubernetes", "oak", {"url": "https://k8s.example.net:6443", "token": "kt",
                                               "flux_repo": "https://me:secret@gitlab.example.com/fleet.git", "apps": APPS})
    job = admin.get(f"/api/jobs/{run(admin, 'oak', 'flux_files')['id']}").get_json()
    assert job["status"] == "failed" and "without user:password" in job["result"]["error"]
    admin.put("/api/integrations/oak", json={"settings": {"flux_repo": str(fleet[0]), "apps": "- {name: x}"}})
    job = admin.get(f"/api/jobs/{run(admin, 'oak', 'flux_files')['id']}").get_json()
    assert job["status"] == "failed" and "either path" in job["result"]["error"]
    assert db.session.scalar(db.select(Job).where(Job.status == "planned")) is None


def test_merge_request_on_a_forge(monkeypatch):
    from labdex import gitsource

    class Forge:
        calls = []

        def get(self, url, **kw):
            self.calls.append(("GET", url, kw["headers"]))
            return type("R", (), {"status_code": 200, "json": lambda s: []})()

        def post(self, url, **kw):
            self.calls.append(("POST", url, kw["json"]))
            return type("R", (), {"status_code": 201, "json": lambda s: {"web_url": "https://gitlab.example.com/infra/fleet/-/merge_requests/4"}})()
    f = Forge()
    url = gitsource.open_merge_request({"url": "https://gitlab.example.com/infra/fleet.git", "branch": "main"}, "labdex/flux-oak", "t",
                                       session=f, token="glpat-example", body="b")
    assert url.endswith("/4") and f.calls[0][2] == {"PRIVATE-TOKEN": "glpat-example"}
    assert f.calls[1][2]["target_branch"] == "main" and f.calls[1][2]["source_branch"] == "labdex/flux-oak" and f.calls[1][2]["description"] == "b"
