"""Workflows: steps in order (templates, actions, waits, health checks) with a schedule and a
maintenance window, their runs, and the run a job belongs to.

Revision ID: 0042_workflows
Revises: 0040_agents
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_workflows"
down_revision = "0040_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "workflow" not in tables:
        op.create_table(
            "workflow",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("description", sa.String(200), nullable=False, server_default=""),
            sa.Column("steps", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("schedule", sa.String(8), nullable=False, server_default=""),
            sa.Column("every_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("days", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("at", sa.String(5), nullable=False, server_default=""),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("window_id", sa.Integer(), sa.ForeignKey("change_window.id", ondelete="SET NULL"), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "workflow_run" not in tables:
        op.create_table(
            "workflow_run",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflow.id", ondelete="SET NULL"), nullable=True),
            sa.Column("name", sa.String(64), nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="running"),
            sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("steps", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("window", sa.Text(), nullable=True),
            sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("note", sa.String(300), nullable=False, server_default=""),
        )
        op.create_index("ix_workflow_run_workflow_id", "workflow_run", ["workflow_id"])
        op.create_index("ix_workflow_run_status", "workflow_run", ["status"])
        op.create_index("ix_workflow_run_created_at", "workflow_run", ["created_at"])
    if "workflow_run_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("job")}:
        with op.batch_alter_table("job") as b:
            b.add_column(sa.Column("workflow_run_id", sa.Integer(), nullable=True))
            b.create_foreign_key("fk_job_workflow_run", "workflow_run", ["workflow_run_id"], ["id"], ondelete="SET NULL")
            b.create_index("ix_job_workflow_run_id", ["workflow_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("job") as b:
        b.drop_index("ix_job_workflow_run_id")
        b.drop_constraint("fk_job_workflow_run", type_="foreignkey")
        b.drop_column("workflow_run_id")
    op.drop_index("ix_workflow_run_created_at", "workflow_run")
    op.drop_index("ix_workflow_run_status", "workflow_run")
    op.drop_index("ix_workflow_run_workflow_id", "workflow_run")
    op.drop_table("workflow_run")
    op.drop_table("workflow")
