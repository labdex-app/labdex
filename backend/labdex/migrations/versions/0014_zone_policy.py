"""0.9.0: the zone policy matrix.

Revision ID: 0014_zone_policy
Revises: 0013_host_status
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_zone_policy"
down_revision = "0013_host_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "zone_policy" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "zone_policy",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("src", sa.String(17), nullable=False),
            sa.Column("dst", sa.String(17), nullable=False),
            sa.Column("ports", sa.Text(), nullable=False),
            sa.Column("description", sa.String(200), nullable=False, server_default=""),
            sa.UniqueConstraint("src", "dst"),
        )


def downgrade() -> None:
    op.drop_table("zone_policy")
