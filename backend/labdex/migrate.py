"""Additive schema upgrades for SQLite.

labdex only ever adds tables and nullable/defaulted columns between versions,
so an upgrade is: create missing tables, then ALTER TABLE ADD COLUMN for
columns the database doesn't have yet. Anything more invasive gets its own
step in STEPS.
"""
from __future__ import annotations

import json

from sqlalchemy import inspect, text

from .models import JSONList, JSONValue, db

# Defaults for columns added to existing rows, where the model default isn't a constant.
BACKFILL = {
    ("integration", "selectors"): json.dumps(["*"]),
    ("service", "publish_srv"): "1",  # services from before 0.5.3 keep their DNS-SD records
}


def _sql_default(table: str, col) -> str | None:
    if (table, col.name) in BACKFILL:
        return BACKFILL[(table, col.name)]
    if isinstance(col.type, JSONList):
        return "[]"
    if isinstance(col.type, JSONValue):
        return None
    d = col.default.arg if col.default is not None and not callable(col.default.arg) else None
    if isinstance(d, bool):
        return "1" if d else "0"
    return None if d is None else str(d)


def upgrade() -> list[str]:
    """Bring an existing database up to the current models. Returns what was done."""
    done = []
    insp = inspect(db.engine)
    existing = set(insp.get_table_names())
    for table in db.metadata.sorted_tables:
        if table.name not in existing:
            continue  # create_all made it
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(db.engine.dialect)}'
            default = _sql_default(table.name, col)
            if default is not None:
                ddl += " DEFAULT " + ("'" + default.replace("'", "''") + "'")
            with db.engine.begin() as conn:
                conn.execute(text(ddl))
            done.append(f"{table.name}.{col.name}")
    return done
