"""Ansible through the agent: the job types an agent runs (from its hello) and the labdex half of
the per-host become opt-in.

Revision ID: 0046_agent_ansible
Revises: 0045_agent_samples
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_agent_ansible"
down_revision = "0045_agent_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}
    with op.batch_alter_table("agent") as b:
        if "jobs" not in have:
            b.add_column(sa.Column("jobs", sa.Text(), nullable=False, server_default="[]"))
        if "ansible_become" not in have:
            b.add_column(sa.Column("ansible_become", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("agent") as b:
        b.drop_column("ansible_become")
        b.drop_column("jobs")
