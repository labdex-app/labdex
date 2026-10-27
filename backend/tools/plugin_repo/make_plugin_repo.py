#!/usr/bin/env python3
"""Turn a plugin folder into a git repository of its own (the layout every labdex plugin
repository uses): the plugin at the root, a README written from its manifest, the MIT
licence, and the release workflow that signs it and attaches it to a GitHub release.

    python3 tools/plugin_repo/make_plugin_repo.py labdex/official_plugins/opnsense ../labdex-plugin-opnsense
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
LICENSE = """MIT License

Copyright (c) 2026 labdex contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def readme(m: dict, official: bool) -> str:
    rows = "\n".join(f"| {f.get('label') or f['key']} | {str(f.get('help') or '').replace('|', '/')} |" for f in m.get("settings") or []
                     if not f["key"].startswith("schedule_"))
    acts = "\n".join(f"- **{a.get('label') or a['id']}** ({a.get('mode', 'run')}){': ' + a['description'] if a.get('description') else ''}"
                     for a in m.get("actions") or [])
    return f"""# {m.get('name', m['id'])}

A [labdex](https://github.com/reykimedev/labdex) plugin. {m.get('description', '')}

**Version** {m.get('version', '')} · **Works with** labdex {m.get('labdex') or 'any'} · **Plugin id** `{m['id']}`

> [!WARNING]
> Built with AI, for home labs only: labdex and its plugins were written largely with an AI
> coding assistant (Claude), directed and reviewed by a human. They haven't had an independent
> security audit and may contain security problems. Run them on a trusted home network.

## Install

{"It ships with labdex: **Plugins › Marketplace**, then *Install* (nothing is downloaded). Updates arrive through the official Marketplace (signed with the labdex project key)." if official else "Add the Marketplace that lists it under Admin › Plugins, then install it from **Plugins › Marketplace**."}

## Settings

| Setting | Notes |
| --- | --- |
{rows or '| — | |'}

## Actions

{acts or '—'}

## Releasing

Bump `version` in `plugin.yaml`, add a `## <version>` section to `CHANGELOG.md`, commit, then
publish a GitHub release tagged `v<version>` (web UI, or `gh release create v<version> --target main --notes ""`).
The release workflow signs the plugin with the `LABDEX_SIGNING_KEY` secret, attaches `{m['id']}-<version>.zip`
and `CHANGELOG.md` to the release and puts that version's CHANGELOG section into its description.
Files listed in `.releaseignore` stay out of the zip.
"""


def main(src: str, dest: str, official: bool = True) -> None:
    s, d = Path(src), Path(dest)
    m = yaml.safe_load((s / "plugin.yaml").read_text())
    d.mkdir(parents=True, exist_ok=True)
    for f in s.iterdir():
        if f.name in ("__pycache__", "SIGNATURE.json") or f.name.startswith("."):
            continue
        if f.is_dir():
            shutil.copytree(f, d / f.name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(f, d / f.name)
    (d / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / ".github" / "workflows" / "release.yml", d / ".github" / "workflows" / "release.yml")
    shutil.copy2(HERE / ".gitattributes", d / ".gitattributes")
    if not (d / ".releaseignore").exists():
        shutil.copy2(HERE / ".releaseignore", d / ".releaseignore")
    (d / ".gitignore").write_text("__pycache__/\n*.pyc\nbuild/\n*.zip\n")
    if not (d / "README.md").exists():
        (d / "README.md").write_text(readme(m, official))
    if not (d / "LICENSE").exists():
        (d / "LICENSE").write_text(LICENSE)
    if not (d / "CHANGELOG.md").exists():
        (d / "CHANGELOG.md").write_text(f"# {m.get('name', m['id'])}\n\n## {m.get('version', '1.0')}\n- First release as its own repository.\n")
    print(f"{m['id']} {m.get('version')}: {d}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2], "--community" not in sys.argv)
