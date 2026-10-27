"""v0.8.1: k3s / Kubernetes."""
import pytest

from labdex.models import HostStatus, db

from .fakes import FakeKube, FakeServer
from .test_plugins import make_instance, run
from .test_v03 import make_tree, put_host


@pytest.fixture
def kube():
    fake = FakeKube()
    srv = FakeServer(fake)
    fake.url = srv.url
    fake.nodes = [FakeKube.node("k3s-a", "10.10.128.11"), FakeKube.node("k3s-b", "10.10.128.12", ready=False, roles=()),
                  FakeKube.node("k3s-c", "10.10.128.99", version="v1.30.5+k3s1", roles=())]
    fake.pods = [{"spec": {"nodeName": "k3s-a"}}] * 3 + [{"spec": {"nodeName": "k3s-b"}}]
    yield fake
    srv.close()


def test_nodes_status_and_drift(admin, kube):
    make_tree(admin)
    put_host(admin, "srv-vm-k3s-01", features=["kube.cluster.oak"])
    put_host(admin, "srv-vm-mon-01", features=["kube.cluster.oak"])
    make_instance(admin, "k3s", "oak", {"url": kube.url, "token": "kt", "feature": "kube.cluster.oak"})
    assert run(admin, "oak", "test")["result"]["message"] == "v1.31.2+k3s1: 3 node(s)"
    job = run(admin, "oak", "poll")
    assert job["result"] == {"nodes": 3, "ready": 2, "drift": 4}, admin.get(f"/api/jobs/{job['id']}").get_json()["log"]
    rows = {r.host: r for r in db.session.scalars(db.select(HostStatus))}
    assert rows["srv-vm-k3s-01"].state == "up" and rows["srv-vm-k3s-02"].state == "down"
    page = admin.get("/api/integrations/oak/pages/cluster").get_json()
    issues = {(d["host"], d["issue"]) for d in page["sections"][1]["rows"]}
    assert ("srv-vm-mon-01", "has kube.cluster.oak but isn't a node") in issues
    assert ("srv-vm-k3s-02", "is a node but lacks kube.cluster.oak") in issues
    assert any("k3s-c" in i and "matches no host" in i for _, i in issues)
    assert any("different versions" in i for _, i in issues)
    nodes = {n["node"]: n for n in page["sections"][0]["rows"]}
    assert nodes["k3s-a"]["roles"] == "control-plane, master" and nodes["k3s-a"]["pods"] == 3 and nodes["k3s-b"]["roles"] == "worker"
