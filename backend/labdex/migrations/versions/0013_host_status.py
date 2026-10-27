"""0.7.2: host status from monitoring sources.

Revision ID: 0013_host_status
Revises: 0012_plugin_repositories
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_host_status"
down_revision = "0012_plugin_repositories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "host_status" not in tables:
        op.create_table(
            "host_status",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("host", sa.String(63), nullable=False),
            sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integration.id", ondelete="CASCADE"), nullable=False),
            sa.Column("state", sa.String(8), nullable=False),
            sa.Column("detail", sa.String(300), nullable=False),
            sa.Column("data", sa.Text(), nullable=True),
            sa.Column("checked_at", sa.DateTime(), nullable=False),
            sa.Column("changed_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("host", "integration_id"),
        )
        op.create_index("ix_host_status_host", "host_status", ["host"])
        op.create_index("ix_host_status_integration_id", "host_status", ["integration_id"])
    if "host_health" not in tables:
        op.create_table(
            "host_health",
            sa.Column("host", sa.String(63), primary_key=True),
            sa.Column("state", sa.String(8), nullable=False),
            sa.Column("since", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("host_health")
    op.drop_table("host_status")
