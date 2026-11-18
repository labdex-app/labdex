"""Agents: enrolled host agents, their one-time enrolment tokens and their jobs.

Revision ID: 0040_agents
Revises: 0039_host_patch
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_agents"
down_revision = "0039_host_patch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "agent" not in tables:
        op.create_table(
            "agent",
            sa.Column("id", sa.String(16), primary_key=True),
            sa.Column("host", sa.String(63), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("enrolled_at", sa.DateTime(), nullable=False),
            sa.Column("enrolled_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("capabilities", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allow_root", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("discovery_every", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("patches_every", sa.Integer(), nullable=False, server_default="360"),
            sa.Column("version", sa.String(32), nullable=False, server_default=""),
            sa.Column("hostname", sa.String(253), nullable=False, server_default=""),
            sa.Column("address", sa.String(64), nullable=False, server_default=""),
            sa.Column("root", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("host_allow", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("facts", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("metrics", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("discovery", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("connected", sa.String(128), nullable=False, server_default=""),
            sa.Column("linked_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_agent_host", "agent", ["host"])
    if "agent_token" not in tables:
        op.create_table(
            "agent_token",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("host", sa.String(63), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("allow_root", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index("ix_agent_token_host", "agent_token", ["host"])
    if "agent_job" not in tables:
        op.create_table(
            "agent_job",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("agent_id", sa.String(16), sa.ForeignKey("agent.id", ondelete="CASCADE"), nullable=False),
            sa.Column("host", sa.String(63), nullable=False),
            sa.Column("type", sa.String(32), nullable=False),
            sa.Column("params", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
            sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("created_by", sa.String(128), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
        )
        op.create_index("ix_agent_job_agent_id", "agent_job", ["agent_id"])
        op.create_index("ix_agent_job_host", "agent_job", ["host"])
        op.create_index("ix_agent_job_status", "agent_job", ["status"])
        op.create_index("ix_agent_job_created_at", "agent_job", ["created_at"])


def downgrade() -> None:
    op.drop_table("agent_job")
    op.drop_table("agent_token")
    op.drop_table("agent")
