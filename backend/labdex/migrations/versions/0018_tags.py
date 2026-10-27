"""0.10.2: tags known everywhere, made from the tags hosts already have.

Revision ID: 0018_tags
Revises: 0017_zones
Create Date: 2026-10-25
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0018_tags"
down_revision = "0017_zones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "tag" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "tag",
            sa.Column("name", sa.String(32), primary_key=True),
            sa.Column("colour", sa.String(7), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
        )
    have = {r[0] for r in bind.execute(sa.text("SELECT name FROM tag"))}
    used = set()
    for (tags,) in bind.execute(sa.text("SELECT tags FROM host")):
        used |= {str(t)[:32] for t in json.loads(tags or "[]") if t}
    for t in sorted(used - have):
        bind.execute(sa.text("INSERT INTO tag (name, colour, description) VALUES (:n, '', '')"), {"n": t})


def downgrade() -> None:
    op.drop_table("tag")
