"""Change sets: inventory changes reviewed as one unit.

Revision ID: 0036_change_sets
Revises: 0035_saved_searches
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_change_sets"
down_revision = "0035_saved_searches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "change_set" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "change_set",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.String(12), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("applied_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("change_set")
