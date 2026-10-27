"""Alembic environment. labdex runs it in-process (labdex.schema), handing over the app's connection."""
from alembic import context

from labdex.models import db

config = context.config
target_metadata = db.metadata


def run_migrations_online() -> None:
    connection = config.attributes["connection"]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite can't ALTER most things: copy-and-swap
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("labdex migrations run online only: flask --app labdex db upgrade")
run_migrations_online()
