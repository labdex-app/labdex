"""1.17: what's printed on a cable, its length and colour; saved topology views.

Revision ID: 0030_cable_labels
Revises: 0029_ports
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_cable_labels"
down_revision = "0029_ports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "label" not in {c["name"] for c in insp.get_columns("link")}:
        with op.batch_alter_table("link") as b:
            b.add_column(sa.Column("label", sa.String(32), nullable=False, server_default=""))
            b.add_column(sa.Column("length", sa.String(12), nullable=False, server_default=""))
            b.add_column(sa.Column("colour", sa.String(16), nullable=False, server_default=""))
    if "map_view" not in insp.get_table_names():
        op.create_table(
            "map_view",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uid", sa.String(12), nullable=False, unique=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("config", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("map_view")
    with op.batch_alter_table("link") as b:
        for c in ("colour", "length", "label"):
            b.drop_column(c)
