"""Server-Timing on every API answer: the time in labdex, in the database and the query count.

A browser's network panel (and a HAR file) then shows whether a slow page waited for SQL, for
Python, or for its turn in the one web process (time the browser saw minus `app`)."""
from __future__ import annotations

import time

from flask import Flask, g, has_request_context, request
from sqlalchemy import event


def init(app: Flask, engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, statement, params, context, executemany):
        conn.info.setdefault("_t", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _stop(conn, cursor, statement, params, context, executemany):
        started = conn.info.get("_t")
        if not started or not has_request_context():
            if started:
                started.pop()
            return
        g._db_s = g.get("_db_s", 0.0) + time.perf_counter() - started.pop()
        g._db_n = g.get("_db_n", 0) + 1

    @app.before_request
    def _begin():
        g._t0 = time.perf_counter()

    @app.after_request
    def _header(resp):
        if "_t0" in g and request.path.startswith("/api/"):
            total = (time.perf_counter() - g._t0) * 1000
            db_ms = g.get("_db_s", 0.0) * 1000
            resp.headers["Server-Timing"] = (f'app;dur={total:.0f}, db;dur={db_ms:.0f};desc="{g.get("_db_n", 0)} queries", '
                                             f"py;dur={max(total - db_ms, 0):.0f}")
        return resp
