"""Database schema: Alembic migrations, run at start-up.

- A new database is created by running every migration.
- A database from labdex 0.4 or older (made with create_all plus additive
  upgrades, no alembic_version table) is first brought to the 0.4 shape with
  the old additive step, then stamped with the baseline, then upgraded.
- Developers add a migration with: flask --app labdex db revision -m "what"
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from .models import db

BASELINE = "0001_baseline"
HERE = Path(__file__).parent / "migrations"


def config(connection=None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.attributes["connection"] = connection
    return cfg


@contextmanager
def migration_connection():
    """A connection for running migrations. On SQLite, foreign keys are off while it runs:
    batch operations rebuild tables, and with them on, rebuilding 'host' would cascade-delete
    its interfaces and services."""
    with db.engine.connect() as conn:
        sqlite = conn.dialect.name == "sqlite"
        if sqlite:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()  # end SQLAlchemy's autobegun transaction (pysqlite never sent a BEGIN for the PRAGMA)
        try:
            with conn.begin():
                yield conn
        finally:
            if sqlite:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                conn.commit()


def current(connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def head() -> str:
    return ScriptDirectory.from_config(config()).get_current_head()


def upgrade() -> list[str]:
    """Bring the database to the newest schema. Returns what was done, for the log."""
    done = []
    with db.engine.begin() as conn:
        rev = current(conn)
        legacy = rev is None and "host" in inspect(conn).get_table_names()
    if legacy:
        from . import migrate
        db.create_all()  # tables added in 0.4 that an older database lacks
        done += migrate.upgrade()
        with migration_connection() as conn:
            command.stamp(config(conn), BASELINE)
        done.append(f"adopted existing database at {BASELINE}")
        rev = BASELINE
    if rev != head():
        with migration_connection() as conn:
            command.upgrade(config(conn), "head")
        done.append(f"migrated {rev or 'empty'} → {head()}")
    return done
