"""0.6.2: notification channels and their outbox.

Revision ID: 0010_notifications
Revises: 0009_approvals_windows
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_notifications"
down_revision = "0009_approvals_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "notification_channel" not in tables:
        op.create_table(
            "notification_channel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("settings", sa.Text(), nullable=True),
            sa.Column("events", sa.Text(), nullable=False),
            sa.Column("integrations", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "notification" not in tables:
        op.create_table(
            "notification",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("channel_id", sa.Integer(), sa.ForeignKey("notification_channel.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event", sa.String(64), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("link", sa.String(500), nullable=False),
            sa.Column("data", sa.Text(), nullable=True),
            sa.Column("status", sa.String(8), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("next_try_at", sa.DateTime(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_notification_channel_id", "notification", ["channel_id"])
        op.create_index("ix_notification_status", "notification", ["status"])
        op.create_index("ix_notification_created_at", "notification", ["created_at"])


def downgrade() -> None:
    op.drop_table("notification")
    op.drop_table("notification_channel")
