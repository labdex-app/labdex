"""0.6.1/0.6.2: approval policies, change windows, approvals on jobs.

Revision ID: 0009_approvals_windows
Revises: 0008_job_queue
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_approvals_windows"
down_revision = "0008_job_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    if "change_window" not in tables:
        op.create_table(
            "change_window",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("days", sa.Text(), nullable=False),
            sa.Column("start", sa.String(5), nullable=False),
            sa.Column("end", sa.String(5), nullable=False),
            sa.Column("timezone", sa.String(64), nullable=False),
            sa.Column("description", sa.String(200), nullable=False, server_default=""),
        )
    if "approval_policy" not in tables:
        op.create_table(
            "approval_policy",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("integrations", sa.Text(), nullable=False),
            sa.Column("zones", sa.Text(), nullable=False),
            sa.Column("triggers", sa.Text(), nullable=False),
            sa.Column("approvals", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("approvers", sa.Text(), nullable=False),
            sa.Column("self_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("delay_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("window_id", sa.Integer(), sa.ForeignKey("change_window.id", ondelete="SET NULL"), nullable=True),
            sa.Column("description", sa.String(200), nullable=False, server_default=""),
        )
    have = {c["name"] for c in insp.get_columns("job")}
    cols = [c for c in (sa.Column("approvals", sa.Text(), nullable=False, server_default="[]"),
                        sa.Column("policy", sa.Text(), nullable=True),
                        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
                        sa.Column("replaces", sa.Integer(), nullable=True),
                        sa.Column("apply_after", sa.DateTime(), nullable=True)) if c.name not in have]
    if cols:
        with op.batch_alter_table("job") as b:
            for c in cols:
                b.add_column(c)


def downgrade() -> None:
    with op.batch_alter_table("job") as b:
        for name in ("apply_after", "replaces", "fingerprint", "policy", "approvals"):
            b.drop_column(name)
    op.drop_table("approval_policy")
    op.drop_table("change_window")
