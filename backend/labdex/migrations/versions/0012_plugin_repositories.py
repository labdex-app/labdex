"""0.7.0: signed plugin repositories.

Revision ID: 0012_plugin_repositories
Revises: 0011_job_templates
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_plugin_repositories"
down_revision = "0011_job_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "plugin_repository" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "plugin_repository",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("url", sa.String(500), nullable=False, unique=True),
            sa.Column("key_id", sa.String(16), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("official", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("added_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=True),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_table("plugin_repository")
