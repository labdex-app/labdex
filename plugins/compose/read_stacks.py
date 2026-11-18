#!/usr/bin/env python3
"""Run on a Docker host by labdex: the stack folders under ROOT as JSON, read-only.
{"name": {"marker": {...} | null, "compose": "..." | null}}"""
import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/opt/stacks"
out = {}
if os.path.isdir(root):
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        entry = {"marker": None, "compose": None}
        try:
            with open(os.path.join(d, ".labdex"), encoding="utf-8") as f:
                entry["marker"] = json.load(f)
        except (OSError, ValueError):
            pass
        for fn in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    entry["compose"] = f.read()
                break
            except OSError:
                continue
        if entry["marker"] or entry["compose"] is not None:
            out[name] = entry
print(json.dumps(out))
