"""Kubernetes workloads & Flux (read-only), against a fake API server."""
import json

import pytest

from labdex import notify

from .fakes import FakeKube, FakeServer
from .test_plugins import make_instance, run


def cond(ok, reason="", message=""):
    return [{"type": "Ready", "status": {True: "True", False: "False", None: "Unknown"}[ok], "reason": reason, "message": message}]


class FakeFluxKube(FakeKube):
    """Adds apps workloads and the Flux API groups; records every method it sees."""

    def __init__(self):
        super().__init__()
        self.methods = set()
        self.flux = True
        self.objects = {"deployments": [], "statefulsets": [], "daemonsets": [], "kustomizations": [], "helmreleases": [],
                        "gitrepositories": [], "ocirepositories": [], "helmrepositories": []}

    def __call__(self, req):
        from werkzeug.wrappers import Response
        self.methods.add(req.method)
        js = lambda d, s=200: Response(json.dumps(d), s, mimetype="application/json")
        if req.headers.get("Authorization") != f"Bearer {self.token}":
            return js({}, 401)
        p = req.path
        if p.startswith("/apis/apps/v1/"):
            return js({"items": self.objects[p.rsplit("/", 1)[1]]})
        groups = {"kustomize.toolkit.fluxcd.io": "v1", "helm.toolkit.fluxcd.io": "v2", "source.toolkit.fluxcd.io": "v1"}
        for g, v in groups.items():
            if p == f"/apis/{g}" and self.flux:
                return js({"name": g, "preferredVersion": {"groupVersion": f"{g}/{v}", "version": v}})
            if p.startswith(f"/apis/{g}/{v}/") and self.flux:
                return js({"items": self.objects[p.rsplit("/", 1)[1]]})
        return super().__call__(req)


@pytest.fixture
def kube():
    fake = FakeFluxKube()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.nodes = [FakeKube.node("oak-1", "10.10.128.11"), FakeKube.node("oak-2", "10.10.128.12", ready=False)]
    o = fake.objects
    o["deployments"] = [
        {"metadata": {"name": "grafana", "namespace": "monitoring"}, "spec": {"replicas": 2, "template": {"spec": {"containers": [{"image": "grafana/grafana:11.3.0"}]}}},
         "status": {"readyReplicas": 2}},
        {"metadata": {"name": "coredns", "namespace": "kube-system"}, "spec": {"replicas": 1}, "status": {"readyReplicas": 0}},
    ]
    o["statefulsets"] = [{"metadata": {"name": "postgres", "namespace": "db"}, "spec": {"replicas": 3}, "status": {"readyReplicas": 1}}]
    o["daemonsets"] = [{"metadata": {"name": "promtail", "namespace": "monitoring"}, "status": {"desiredNumberScheduled": 2, "numberReady": 2}}]
    o["gitrepositories"] = [{"metadata": {"name": "flux-system", "namespace": "flux-system"},
                             "spec": {"url": "ssh://git@gitlab.example.com/infra/fleet.git", "ref": {"branch": "main"}},
                             "status": {"conditions": cond(True), "artifact": {"revision": "main@sha1:4f2c1e0"}}}]
    o["kustomizations"] = [
        {"metadata": {"name": "apps", "namespace": "flux-system"},
         "spec": {"path": "./clusters/oak/apps", "sourceRef": {"kind": "GitRepository", "name": "flux-system"}},
         "status": {"conditions": cond(True, "ReconciliationSucceeded"), "lastAppliedRevision": "main@sha1:4f2c1e0"}},
        {"metadata": {"name": "infra", "namespace": "flux-system"}, "spec": {"path": "./infra", "suspend": True,
                                                                              "sourceRef": {"kind": "GitRepository", "name": "flux-system"}},
         "status": {"conditions": cond(True)}},
    ]
    o["helmreleases"] = [{"metadata": {"name": "loki", "namespace": "monitoring"},
                          "spec": {"chart": {"spec": {"chart": "loki", "version": "6.x", "sourceRef": {"kind": "HelmRepository", "name": "grafana",
                                                                                                       "namespace": "flux-system"}}}},
                          "status": {"conditions": cond(True), "history": [{"chartVersion": "6.16.0"}]}}]
    yield fake
    srv.close()


def setup(admin, kube, **extra):
    make_instance(admin, "kubernetes", "oak", {"url": kube.url, "token": "kt", "cluster": "oak", **extra})


def page(admin):
    return admin.get("/api/integrations/oak/pages/cluster").get_json()


def test_reads_workloads_and_flux_without_writing(admin, kube):
    setup(admin, kube)
    assert run(admin, "oak", "test")["result"]["message"] == "v1.31.2+k3s1: 2 node(s), 3 workload(s), Flux: 3 kustomizations/releases"
    job = run(admin, "oak", "poll")
    assert job["result"] == {"workloads": 3, "not_ready": 1, "flux": 4, "flux_failed": 0}
    assert kube.methods == {"GET"}                                                       # read-only
    p = page(admin)
    summary = {s["label"]: s["value"] for s in p["summary"]}
    assert summary["nodes ready"] == "1 of 2" and summary["workloads not ready"] == 1 and summary["Flux failing"] == 0
    flux, sources, loads = p["sections"]
    assert [r["ref"] for r in flux["rows"]] == ["flux-system/infra", "monitoring/loki", "flux-system/apps"]  # suspended first, then ok by kind
    infra, loki, apps = flux["rows"]
    assert infra["state"] == "suspended" and apps["revision"] == "main@sha1:4f2c1e0" and apps["source"] == "GitRepository/flux-system/flux-system"
    assert loki["revision"] == "6.16.0" and loki["detail"] == "loki 6.x" and loki["source"] == "HelmRepository/flux-system/grafana"
    assert sources["rows"][0]["detail"] == "ssh://git@gitlab.example.com/infra/fleet.git main"
    assert loads["rows"][0]["ref"] == "db/postgres" and loads["rows"][0]["state"] == "degraded" and loads["rows"][0]["replicas"] == "1/3"
    assert "kube-system/coredns" not in [r["ref"] for r in loads["rows"]]               # left-out namespace


def test_notifies_when_something_starts_failing(admin, kube, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "emit", lambda event, title, *a, **kw: sent.append((event, title)) or 1)
    setup(admin, kube)
    run(admin, "oak", "poll")
    assert sent == []                                                                    # the first read only learns
    kube.objects["kustomizations"][0]["status"]["conditions"] = cond(False, "BuildFailed", "kustomize build failed: missing resource")
    kube.objects["deployments"][0]["status"]["readyReplicas"] = 0
    job = run(admin, "oak", "poll")
    assert job["result"]["flux_failed"] == 1
    assert sorted(sent) == [("plugin.kubernetes.flux.failed", "oak: Kustomization flux-system/apps failed"),
                            ("plugin.kubernetes.workload.degraded", "oak: Deployment monitoring/grafana has 0 of 2 ready")]
    run(admin, "oak", "poll")
    assert len(sent) == 2                                                                # still failing: no repeat


def test_cluster_without_flux(admin, kube):
    kube.flux = False
    setup(admin, kube, ignore_namespaces="")
    assert run(admin, "oak", "test")["result"]["message"].endswith("4 workload(s), no Flux objects")
    assert run(admin, "oak", "poll")["result"]["flux"] == 0


def test_bad_token(admin, kube):
    make_instance(admin, "kubernetes", "oak", {"url": kube.url, "token": "nope"})
    job = run(admin, "oak", "test")
    assert job["status"] == "failed" and "access denied" in job["result"]["error"]
