"""1.11: hosts, networks and zones get a short stable id, so their URLs survive a rename.

Revision ID: 0025_uids
Revises: 0024_long_zone_codes
Create Date: 2026-10-26
"""
import secrets

from alembic import op
import sqlalchemy as sa

revision = "0025_uids"
down_revision = "0024_long_zone_codes"
branch_labels = None
depends_on = None

TABLES = {"host": "id", "network": "id", "zone": "code"}
ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"


def _uid() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(10))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, key in TABLES.items():
        if "uid" in {c["name"] for c in insp.get_columns(table)}:
            continue
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column("uid", sa.String(12), nullable=True))
        t = sa.table(table, sa.column(key), sa.column("uid"))
        used = set()
        for (k,) in bind.execute(sa.select(t.c[key])).all():
            u = _uid()
            while u in used:
                u = _uid()
            used.add(u)
            bind.execute(t.update().where(t.c[key] == k).values(uid=u))
        with op.batch_alter_table(table) as b:
            b.alter_column("uid", existing_type=sa.String(12), nullable=False)
            b.create_unique_constraint(f"uq_{table}_uid", ["uid"])


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as b:
            b.drop_constraint(f"uq_{table}_uid", type_="unique")
            b.drop_column("uid")
