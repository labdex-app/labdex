"""Move labdex to its next version, in every place that carries it.

    python tools/bump.py sub      1.9.0 → 1.9.1 (a small release)
    python tools/bump.py minor    1.9.0 → 1.10.0
    python tools/bump.py major    1.9.0 → 2.0.0
    python tools/bump.py patch    1.9.0 → 1.9.0-1, 1.9.0-1 → 1.9.0-2 (a hotfix)
    python tools/bump.py 1.9.0-3  that exact version

Writes labdex/__init__.py, pyproject.toml (as PEP 440: 1.9.0.post1), frontend/package.json
and a new heading at the top of CHANGELOG.md."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from labdex import versioning  # noqa: E402


def main(arg: str) -> str:
    init = ROOT / "backend/labdex/__init__.py"
    cur = versioning.parse(re.search(r'__version__ = "([^"]+)"', init.read_text()).group(1))
    new = cur.next(arg) if arg in ("major", "minor", "sub", "patch") else versioning.parse(arg)
    if new <= cur:
        raise SystemExit(f"{new} doesn't come after {cur}")
    init.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', init.read_text()))
    py = ROOT / "backend/pyproject.toml"
    py.write_text(re.sub(r'^version = "[^"]+"', f'version = "{new.pep440}"', py.read_text(), count=1, flags=re.M))
    pkg = ROOT / "frontend/package.json"
    data = json.loads(pkg.read_text())
    data["version"] = str(new)
    pkg.write_text(json.dumps(data, indent=2) + "\n")
    log = ROOT / "CHANGELOG.md"
    text = log.read_text()
    if f"## {new}\n" not in text:
        what = "Fixes" if new.patch else "Changes"
        log.write_text(text.replace("# Changelog\n\n", f"# Changelog\n\n## {new}\n\n### {what}\n- \n\n", 1))
    return f"{cur} → {new}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(main(sys.argv[1]))
