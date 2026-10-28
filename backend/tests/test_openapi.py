"""The OpenAPI description matches the routes, and the documented paths exist."""
import re
from pathlib import Path

from labdex import openapi

ROOT = Path(__file__).resolve().parents[2]


def spec(app):
    return openapi.build(app)


def test_every_route_is_described(app):
    s = spec(app)
    have = {(p, m) for p, ops in s["paths"].items() for m in ops}
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        path = re.sub(r"<(?:[a-z]+(?:\([^)]*\))?:)?([A-Za-z_]+)>", r"{\1}", rule.rule)
        for m in rule.methods - {"HEAD", "OPTIONS"}:
            assert (path, m.lower()) in have, (path, m)


def test_structure(app):
    s = spec(app)
    assert s["openapi"].startswith("3.0") and s["info"]["version"]
    ids = [op["operationId"] for ops in s["paths"].values() for op in ops.values()]
    assert len(ids) == len(set(ids)), "operationIds must be unique"
    for path, ops in s["paths"].items():
        declared = set(re.findall(r"{([A-Za-z_]+)}", path))
        for op in ops.values():
            assert {p["name"] for p in op.get("parameters", [])} == declared, path
            assert op["summary"] and op["tags"]
    login = s["paths"]["/api/auth/login"]["post"]
    assert login["security"] == []                      # public
    assert "security" not in s["paths"]["/api/hosts"]["get"]  # signed in, the global default
    assert s["paths"]["/api/admin/bindings/{bid}"]["delete"]["parameters"][0]["schema"]["type"] == "integer"


def test_served_and_dumped(admin):
    r = admin.get("/api/openapi.json")
    assert r.status_code == 200 and "/api/hosts/{name}" in r.get_json()["paths"]
    from flask.testing import FlaskCliRunner
    out = FlaskCliRunner(admin.application).invoke(args=["openapi"])
    assert out.exit_code == 0 and '"openapi": "3.0.3"' in out.output


def test_documented_paths_exist(app):
    """The API table in the README and in docs/develop/api.md only lists routes that exist."""
    def norm(p):
        p = re.sub(r"\[[^\]]*\]", "", p).split("?")[0]  # optional parts: /api/jobs[/<id>] is /api/jobs
        return re.sub(r"<[^>]+>|\{[^}]+\}", "*", p)

    exist = {norm(p) for p in spec(app)["paths"]}
    missing = []
    for doc in (ROOT / "README.md", ROOT / "docs/develop/api.md"):
        for row in doc.read_text(encoding="utf-8").splitlines():
            cells = row.split("|")
            if not row.startswith("| ") or len(cells) < 4:
                continue
            for p in re.findall(r"`(/api/[^`\s]*)`", cells[2]):
                if "{" in p and "," in p:  # {areas,trust}: several paths in one
                    continue
                if norm(p) not in exist and not any(e.startswith(norm(p).rstrip('*')) for e in exist):
                    missing.append((doc.name, p))
    assert not missing, missing
