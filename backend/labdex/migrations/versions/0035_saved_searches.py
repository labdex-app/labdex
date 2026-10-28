"""Saved searches of the Hosts page.

Revision ID: 0035_saved_searches
Revises: 0034_trace_macs_oidc
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_saved_searches"
down_revision = "0034_trace_macs_oidc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "saved_search" in insp.get_table_names():
        return
    op.create_table(
        "saved_search",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.String(12), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False, server_default=""),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner", "name"),
    )
    op.create_index("ix_saved_search_owner", "saved_search", ["owner"])


def downgrade() -> None:
    op.drop_table("saved_search")
