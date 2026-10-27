"""A portable copy of the whole database as JSON, for any backend.

Used by backups on PostgreSQL (SQLite backups also keep the raw .db file) and by
`flask --app labdex db copy`, which moves an instance from SQLite to PostgreSQL
or back. Rows are written table by table in dependency order with the schema
revision; loading requires the same revision, so migrate first.
"""
from __future__ import annotations

import base64
import json
from datetime import date, datetime

from sqlalchemy import DateTime, LargeBinary, insert, text

from .models import db

FORMAT = 1


def _out(v):
    if isinstance(v, datetime | date):
        return {"$dt": v.isoformat()}
    if isinstance(v, bytes | memoryview):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    return v


def _in(col, v):
    if isinstance(v, dict) and len(v) == 1:
        if "$dt" in v and isinstance(col.type, DateTime):
            return datetime.fromisoformat(v["$dt"])
        if "$b64" in v and isinstance(col.type, LargeBinary):
            return base64.b64decode(v["$b64"])
    return v


def dump(connection) -> dict:
    from . import schema
    tables = {}
    for t in db.metadata.sorted_tables:
        rows = connection.execute(t.select().order_by(*t.primary_key.columns)).mappings()
        tables[t.name] = [{k: _out(v) for k, v in r.items()} for r in rows]
    return {"format": FORMAT, "revision": schema.current(connection), "tables": tables}


def dumps(connection) -> bytes:
    return json.dumps(dump(connection), separators=(",", ":")).encode()


def load(connection, data: dict) -> dict:
    """Replace every row in the connected database with the dump. Returns row counts."""
    from . import schema
    if data.get("format") != FORMAT:
        raise ValueError("not a labdex database dump")
    here = schema.current(connection)
    if data.get("revision") != here:
        raise ValueError(f"the dump is at schema {data.get('revision')}, this database at {here}: migrate both to the same version")
    counts = {}
    for t in reversed(db.metadata.sorted_tables):
        connection.execute(t.delete())
    for t in db.metadata.sorted_tables:
        rows = data["tables"].get(t.name) or []
        cols = {c.name: c for c in t.columns}
        rows = [{k: _in(cols[k], v) for k, v in r.items() if k in cols} for r in rows]
        # self references (a VM's runs_on host) are filled in after all rows exist
        selfref = [c.name for c in t.columns for fk in c.foreign_keys if fk.column.table is t]
        later = [(r[t.primary_key.columns[0].name], {c: r[c] for c in selfref}) for r in rows if any(r.get(c) is not None for c in selfref)]
        if rows:
            connection.execute(insert(t), [{**r, **dict.fromkeys(selfref)} for r in rows])
        pk = t.primary_key.columns[0] if selfref else None
        for key, vals in later:
            connection.execute(t.update().where(pk == key).values(**vals))
        counts[t.name] = len(rows)
    if connection.dialect.name == "postgresql":
        for t in db.metadata.sorted_tables:
            pk = list(t.primary_key.columns)
            if len(pk) == 1 and pk[0].autoincrement in (True, "auto") and pk[0].type.python_type is int:
                connection.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', '{pk[0].name}'), "
                    f"COALESCE((SELECT MAX(\"{pk[0].name}\") FROM \"{t.name}\"), 0) + 1, false)"))
    return counts

