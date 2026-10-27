"""OPNsense alias export, in the same shape as Firewall → Aliases → Download.

Re-importing updates aliases by name. The firewall name is the alias's
firewall id (at most 32 characters); the full labdex name goes into the
description. UUIDs are derived from the id, so diffs stay small.
"""
from __future__ import annotations

import json
import uuid

from .aliases import Alias, all_aliases

NAMESPACE = uuid.UUID("6f1d3c3e-6f5b-4b3e-9a57-6c6162646578")  # ends in "labdex" in hex
MARKER = "[labdex]"  # in descriptions of file exports and of aliases synced before categories
CATEGORY = "automation:labdex"


def description(a: Alias, marker: bool = True) -> str:
    text = a.description or ""
    if (a.fw_id or a.name) != a.name:
        text = f"{a.name}: {text}" if text else a.name
    return f"{text} {MARKER}".strip() if marker else text[:255]


def _entry(a: Alias) -> dict:
    return {
        "enabled": "1",
        "name": a.fw_id or a.name,
        "type": a.type,
        "proto": "",
        "interface": "",
        "counters": "0",
        "updatefreq": "",
        "content": "\n".join(a.content),
        "categories": "",
        "description": description(a),
    }


def export(aliases: list[Alias] | None = None) -> dict:
    aliases = all_aliases() if aliases is None else aliases
    # Nested aliases must exist before the aliases that reference them, so emit
    # leaves first: plain content, then groups in dependency order.
    names = {a.fw_id or a.name for a in aliases}
    done, ordered = set(), []
    pending = list(aliases)
    while pending:
        progressed = False
        for a in list(pending):
            deps = {c for c in a.content if c in names}
            if deps <= done:
                ordered.append(a)
                done.add(a.fw_id or a.name)
                pending.remove(a)
                progressed = True
        if not progressed:  # cycle: emit the rest as-is, the linter reports it
            ordered.extend(pending)
            break
    return {"aliases": {"alias": {str(uuid.uuid5(NAMESPACE, a.fw_id or a.name)): _entry(a) for a in ordered}}}


def export_json(aliases: list[Alias] | None = None) -> str:
    return json.dumps(export(aliases), indent=2) + "\n"
