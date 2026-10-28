"""What a pull request needs CI to check, from what it changes (the "What changed" job).

Prints KEY=value lines for $GITHUB_OUTPUT:

    backend   the backend tests (by pytest-testmon: only the tests the change affects)
    full      every backend test (dependencies, migrations, plugins, CI itself)
    dbs       ["sqlite"], or with "postgres" where they can differ (database code, a release)
    e2e       the UI build and the browser tests (frontend, or the API it calls)
    perf      the performance budget (backend code)
    docs      the docs build
    image     the Docker image build (Dockerfile, dependencies)

A version bump (bump.py) changes nothing that needs testing: the version lines of
pyproject.toml, package.json, package-lock.json and labdex/__init__.py are ignored.

    python3 .github/ci-changes.py BASE_REF     (no BASE_REF, or FULL=true: everything)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib

DB_CODE = re.compile(r"db\.(session|select|func|engine)|\bsa\.|sqlalchemy|mapped_column|relationship\(|\.where\(|"
                     r"\.filter(_by)?\(|\border_by\(|\bgroup_by\(|\bfunc\.|\btext\(|\bilike\(|\.like\(|\.query\(")
VERSION_LINE = re.compile(r'^\s*(__version__\s*=|version\s*=|"version"\s*:)')


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def at(ref: str, path: str) -> str:
    try:
        return git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return ""


def changed_lines(base: str, path: str) -> list[str]:
    out = git("diff", "-U0", f"{base}...HEAD", "--", path)
    return [ln[1:] for ln in out.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


def python_deps_changed(base: str) -> bool:
    def deps(text: str) -> dict:
        if not text:
            return {}
        d = tomllib.loads(text)
        p = d.get("project", {})
        return {"deps": p.get("dependencies"), "extras": p.get("optional-dependencies"), "python": p.get("requires-python"),
                "build": d.get("build-system")}
    return deps(at(base, "backend/pyproject.toml")) != deps(at("HEAD", "backend/pyproject.toml"))


def js_deps_changed(base: str) -> bool:
    def pkg(text: str) -> dict:
        d = json.loads(text) if text else {}
        return {k: d.get(k) for k in ("dependencies", "devDependencies", "overrides", "engines")}

    def lock(text: str) -> dict:
        d = json.loads(text) if text else {}
        d.pop("version", None)
        root = (d.get("packages") or {}).get("")
        if isinstance(root, dict):
            root.pop("version", None)
        return d
    return (pkg(at(base, "frontend/package.json")) != pkg(at("HEAD", "frontend/package.json"))
            or lock(at(base, "frontend/package-lock.json")) != lock(at("HEAD", "frontend/package-lock.json")))


def main() -> None:
    base_ref = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    full = os.environ.get("FULL") == "true" or not base_ref
    out = dict.fromkeys(("backend", "full", "e2e", "perf", "docs", "image"), False)
    pg = full
    if full:
        out = dict.fromkeys(out, True)
    else:
        base = f"origin/{base_ref}"
        files = [f for f in git("diff", "--name-only", f"{base}...HEAD").splitlines() if f]
        # a version bump alone: those lines don't count
        version_only = {f for f in files
                        if f in ("backend/pyproject.toml", "frontend/package.json", "frontend/package-lock.json", "backend/labdex/__init__.py")
                        and all(VERSION_LINE.match(ln) or not ln.strip() for ln in changed_lines(base, f))}
        if "frontend/package-lock.json" in files and not js_deps_changed(base):
            version_only.add("frontend/package-lock.json")
        real = [f for f in files if f not in version_only]
        print("changed:", *(f"  {f}" for f in files), sep="\n", file=sys.stderr)
        if version_only:
            print("version bump only:", ", ".join(sorted(version_only)), file=sys.stderr)
        py_deps = "backend/pyproject.toml" in real and python_deps_changed(base)
        js_deps = any(f.startswith("frontend/package") for f in real) and js_deps_changed(base)

        def has(rx: str) -> bool:
            return any(re.search(rx, f) for f in real)

        everything = py_deps or has(r"^\.github/workflows/ci\.yml$|^\.github/ci-changes\.py$")
        if everything:
            out = dict.fromkeys(out, True)
            pg = True
        else:
            code = has(r"^backend/labdex/(?!static/).*") or has(r"^examples/")
            out["full"] = has(r"^backend/labdex/migrations/|^backend/labdex/models\.py$|^plugins/|^\.gitmodules$")
            out["backend"] = code or out["full"] or has(r"^backend/tests/|^backend/tools/|^backend/pyproject\.toml$")
            out["perf"] = has(r"^backend/labdex/(?!static/|migrations/).*\.py$")
            out["e2e"] = js_deps or has(r"^frontend/(?!package)") or has(r"^backend/labdex/api/") or has(r"^backend/tests/e2e/")
            out["docs"] = has(r"^docs/|^mkdocs\.yml$|^CHANGELOG\.md$|^README\.md$")
            out["image"] = js_deps or has(r"^Dockerfile$|^\.dockerignore$")
            pg = out["full"] or base_ref.startswith("release/")
            if not pg and out["backend"]:
                pg = any(DB_CODE.search(ln) for f in real if f.endswith(".py") and f.startswith(("backend/labdex/", "plugins/"))
                         for ln in changed_lines(base, f))
    for k, v in out.items():
        print(f"{k}={'true' if v else 'false'}")
    print("dbs=" + json.dumps(["sqlite", "postgres"] if pg and out["backend"] else ["sqlite"]))


if __name__ == "__main__":
    main()
