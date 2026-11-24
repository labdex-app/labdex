"""Host agent metrics every 5 minutes for 48 hours, for the charts on the host page.

Revision ID: 0045_agent_samples
Revises: 0044_host_health_events
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_agent_samples"
down_revision = "0044_host_health_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "agent_sample" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "agent_sample",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(63), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("cpu", sa.Float(), nullable=True),
        sa.Column("mem", sa.Float(), nullable=True),
        sa.Column("disk", sa.Float(), nullable=True),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("net_rx", sa.BigInteger(), nullable=True),
        sa.Column("net_tx", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_agent_sample_host", "agent_sample", ["host"])
    op.create_index("ix_agent_sample_at", "agent_sample", ["at"])


def downgrade() -> None:
    op.drop_index("ix_agent_sample_at", "agent_sample")
    op.drop_index("ix_agent_sample_host", "agent_sample")
    op.drop_table("agent_sample")
