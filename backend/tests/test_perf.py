"""Performance budget on 1000 generated hosts.

Runs only with LABDEX_PERF=1 (CI does). Budgets are generous for slow CI
runners; each request is timed warm (second call), the way users see it.
"""
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


def test_budget_1000_hosts(big):
    results, over = {}, []
    for path, budget in BUDGET_MS.items():
        assert big.get(path).status_code == 200, path  # warm the per-revision caches
        t = time.perf_counter()
        r = big.get(path)
        ms = (time.perf_counter() - t) * 1000
        assert r.status_code == 200
        results[path] = ms
        if ms > budget:
            over.append(f"{path}: {ms:.0f} ms > {budget} ms")
    print("\n" + "\n".join(f"{ms:8.1f} ms  {p}" for p, ms in results.items()))
    assert not over, over
    # unchanged answers revalidate to an empty 304
    tag = big.get("/api/hosts").headers["ETag"]
    assert big.get("/api/hosts", headers={"If-None-Match": tag}).status_code == 304
