"""Performance budget on 1000 generated hosts.

Runs only with LABDEX_PERF=1 (CI does). Budgets are generous for slow CI
runners; each request is timed warm (second call), the way users see it.
"""
import gc
import os
import sys
import time
from pathlib import Path

import pytest

from labdex import inventory

from .conftest import ADMIN, client

pytestmark = pytest.mark.skipif(os.environ.get("LABDEX_PERF") != "1", reason="set LABDEX_PERF=1")

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from big_inventory import build

BUDGET_MS = {
    "/api/hosts": 500,
    "/api/hosts?fields=name,fun,zone,can_edit": 150,
    "/api/hosts?q=web&limit=50": 400,
    "/api/hosts/srv-vm-web000-01": 120,
    "/api/lint": 600,
    "/api/networks": 400,
    "/api/aliases": 600,
    "/api/services": 600,
    "/api/nav": 400,
    "/api/meta": 150,
    "/api/history?limit=50": 200,
}


@pytest.fixture
def big(app):
    import yaml
    inventory.import_yaml(yaml.safe_dump(build(1000), sort_keys=False))
    c = client(app)
    assert c.post("/api/auth/setup", json=ADMIN).status_code == 201
    return c


def _full_collections():
    """Time Python's full garbage collections. One costs ~100 ms with 1000 hosts loaded and
    lands in whichever request sets it off, so a slow number with one is noise, not the route."""
    spans = []

    def cb(phase, info):
        if info["generation"] == 2:
            if phase == "start":
                spans.append(time.perf_counter())
            else:
                spans[-1] = (time.perf_counter() - spans[-1]) * 1000
    return spans, cb


def test_budget_1000_hosts(big):
    results, over = {}, []
    spans, cb = _full_collections()
    gc.callbacks.append(cb)
    try:
        for path, budget in BUDGET_MS.items():
            assert big.get(path).status_code == 200, path  # warm the per-revision caches
            n = len(spans)
            t = time.perf_counter()
            r = big.get(path)
            ms = (time.perf_counter() - t) * 1000
            assert r.status_code == 200
            gcs = f" (incl. full gc {sum(spans[n:]):.0f} ms)" if len(spans) > n else ""
            results[path] = (ms, gcs)
            if ms > budget:
                over.append(f"{path}: {ms:.0f} ms > {budget} ms{gcs}")
    finally:
        gc.callbacks.remove(cb)
    print("\n" + "\n".join(f"{ms:8.1f} ms  {p}{gcs}" for p, (ms, gcs) in results.items()))
    assert not over, over
    # unchanged answers revalidate to an empty 304
    tag = big.get("/api/hosts").headers["ETag"]
    assert big.get("/api/hosts", headers={"If-None-Match": tag}).status_code == 304
