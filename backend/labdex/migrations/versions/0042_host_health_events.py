"""Changes of a host's combined state, for the 48 h health strip.

Revision ID: 0042_host_health_events
Revises: 0041_user_prefs
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_host_health_events"
down_revision = "0041_user_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "host_health_event" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "host_health_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(63), nullable=False),
        sa.Column("state", sa.String(8), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_host_health_event_host", "host_health_event", ["host"])
    op.create_index("ix_host_health_event_at", "host_health_event", ["at"])


def downgrade() -> None:
    op.drop_table("host_health_event")
