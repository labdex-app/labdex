"""Per-user preferences: the sidebar's pins, hidden pages and order.

Revision ID: 0043_user_prefs
Revises: 0042_workflows
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_user_prefs"
down_revision = "0042_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}
    if "prefs" not in have:
        with op.batch_alter_table("user") as b:
            b.add_column(sa.Column("prefs", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.drop_column("prefs")
