"""0.6.3: job templates.

Revision ID: 0011_job_templates
Revises: 0010_notifications
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_job_templates"
down_revision = "0010_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "job_template" not in set(insp.get_table_names()):
        op.create_table(
            "job_template",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("integration_id", sa.Integer(), sa.ForeignKey("integration.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("host", sa.String(63), nullable=False, server_default=""),
            sa.Column("params", sa.Text(), nullable=True),
            sa.Column("description", sa.String(200), nullable=False, server_default=""),
            sa.Column("schedule", sa.String(8), nullable=False, server_default=""),
            sa.Column("every_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("days", sa.Text(), nullable=False),
            sa.Column("at", sa.String(5), nullable=False, server_default=""),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_job_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "template_id" not in {c["name"] for c in insp.get_columns("job")}:
        with op.batch_alter_table("job") as b:
            b.add_column(sa.Column("template_id", sa.Integer(), nullable=True))
            b.create_foreign_key("fk_job_template_id", "job_template", ["template_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("job") as b:
        b.drop_constraint("fk_job_template_id", type_="foreignkey")
        b.drop_column("template_id")
    op.drop_table("job_template")
