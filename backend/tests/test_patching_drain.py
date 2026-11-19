"""Patching Kubernetes nodes: drain first, patch, wait for Ready, uncordon, one node at a time.
The API server and Ansible are faked: nothing reaches a cluster or a host."""
import json

import pytest

from labdex import patches

from .fakes import FakeKube, FakeServer
from .test_patching_plugin import DNS, MON, runs  # noqa: F401  (fixture)


class DrainKube(FakeKube):
    """Nodes you can cordon, pods you can evict; `held` pods answer 429 like a PodDisruptionBudget."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.held = set()

    def __call__(self, req):
        from werkzeug.wrappers import Response
        js = lambda d, s=200: Response(json.dumps(d), s, mimetype="application/json")
        if req.headers.get("Authorization") != f"Bearer {self.token}":
            return js({}, 401)
        p = req.path
        if p.startswith("/api/v1/nodes/"):
            node = next((n for n in self.nodes if n["metadata"]["name"] == p.rsplit("/", 1)[1]), None)
            if node is None:
                return js({}, 404)
            if req.method == "PATCH":
                assert req.headers.get("Content-Type") == "application/merge-patch+json"
                on = bool((req.get_json(force=True).get("spec") or {}).get("unschedulable"))
                node["spec"]["unschedulable"] = on
                self.calls.append(("cordon" if on else "uncordon", node["metadata"]["name"]))
            return js(node)
        if p == "/api/v1/pods" and req.args.get("fieldSelector", "").startswith("spec.nodeName="):
            on = req.args["fieldSelector"].split("=", 1)[1]
            return js({"items": [x for x in self.pods if x["spec"]["nodeName"] == on]})
        if p.endswith("/eviction") and req.method == "POST":
            ns, name = p.split("/")[4], p.split("/")[6]
            if f"{ns}/{name}" in self.held:
                return js({"kind": "Status", "code": 429}, 429)
            self.pods = [x for x in self.pods if (x["metadata"]["namespace"], x["metadata"]["name"]) != (ns, name)]
            self.calls.append(("evict", f"{ns}/{name}"))
            return js({"kind": "Status", "code": 201}, 201)
        return super().__call__(req)


def pod(name, node, ns="apps", **meta):
    return {"metadata": {"name": name, "namespace": ns, **meta}, "spec": {"nodeName": node}, "status": {"phase": "Running"}}


@pytest.fixture
def kube(monkeypatch):
    # a clock that jumps when the plugin waits, so timeouts run out at once
    import time
    real, skipped = time.monotonic, [0.0]
    monkeypatch.setattr("time.sleep", lambda s: skipped.__setitem__(0, skipped[0] + s))
    monkeypatch.setattr("time.monotonic", lambda: real() + skipped[0])
    fake = DrainKube()
    srv = FakeServer(fake)
    fake.nodes = [FakeKube.node(DNS, "10.0.0.11")]  # the host is a node of the cluster
    fake.pods = [pod("web-1", DNS), pod("cron-1", DNS),
                 pod("promtail-x", DNS, "monitoring", ownerReferences=[{"kind": "DaemonSet", "name": "promtail"}]),
                 pod("kube-vip", DNS, "kube-system", annotations={"kubernetes.io/config.mirror": "abc"}),
                 pod("other-1", "elsewhere")]
    fake.pods[1]["status"] = {"phase": "Succeeded"}
    fake.url = srv.url
    yield fake
    srv.close()


@pytest.fixture
def patching(admin, kube):
    r = admin.post("/api/integrations", json={"plugin_id": "patching", "name": "home-patching"})
    assert r.status_code == 201, r.get_json()
    r = admin.put("/api/integrations/home-patching", json={"enabled": True, "settings": {
        "reboot": "if_needed", "kube_url": kube.url, "kube_token": "kt", "drain_timeout": 1}})
    assert r.status_code == 200, r.get_json()
    r = admin.post("/api/approval-policies", json={"name": "no approvals", "approvals": 0})
    assert r.status_code == 201, r.get_json()
    return admin


def plan(c):
    r = c.post("/api/integrations/home-patching/actions/patch", json={"params": {}})
    assert r.status_code == 202, r.get_json()
    return c.get(f"/api/jobs/{r.get_json()['id']}").get_json()


def approve(c, job):
    c.post(f"/api/jobs/{job['id']}/approve", json={})
    return c.get(f"/api/jobs/{job['id']}").get_json()


def test_a_node_is_drained_patched_and_uncordoned(patching, kube, runs):  # noqa: F811
    patches.report("agent", {DNS: {"pending": 2}, MON: {"pending": 1}})
    job = plan(patching)
    by = {c["name"]: c for c in job["plan"]}
    assert "Kubernetes node srv-vm-dns-01: drained first" in by[DNS]["summary"] and "node" not in by[MON]["after"]
    assert kube.calls == []                                           # planning reads the cluster only
    job = approve(patching, job)
    assert job["status"] == "applied", job
    # other hosts first, in one run; then the node on its own
    assert [r["limit"] for r in runs] == [MON, DNS]
    assert kube.calls == [("cordon", DNS), ("evict", "apps/web-1"), ("uncordon", DNS)]  # DaemonSet, mirror, finished pods stay
    assert not kube.nodes[0]["spec"]["unschedulable"] and job["result"]["nodes"] == [DNS]


def test_a_held_pod_stops_the_run_with_the_node_cordoned(patching, kube, runs):  # noqa: F811
    kube.held.add("apps/web-1")
    patches.report("agent", {DNS: {"pending": 2}})
    job = approve(patching, plan(patching))
    assert job["status"] == "failed", job
    assert "couldn't drain it in time (apps/web-1); it stays cordoned" in (job.get("log") or "")
    assert runs == [] and kube.nodes[0]["spec"]["unschedulable"] is True   # never patched, never uncordoned


def test_a_node_that_does_not_come_back_stays_cordoned(patching, kube, runs, monkeypatch):  # noqa: F811
    patches.report("agent", {DNS: {"pending": 2}})
    job = plan(patching)
    kube.nodes[0]["status"]["conditions"][0]["status"] = "False"
    patching.put("/api/integrations/home-patching", json={"settings": {
        "reboot": "if_needed", "kube_url": kube.url, "kube_token": "kt", "drain_timeout": 1, "reboot_timeout": 1}})
    job = approve(patching, job)
    assert job["status"] == "failed" and "not Ready after patching; it stays cordoned" in (job.get("log") or ""), job
    assert len(runs) == 1 and ("uncordon", DNS) not in kube.calls
