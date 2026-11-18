"""Patch state: what each source last said about a host's pending updates and reboots.

Revision ID: 0039_host_patch
Revises: 0038_site_git
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_host_patch"
down_revision = "0038_site_git"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "host_patch" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "host_patch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(63), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="agent"),
        sa.Column("pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("security", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("packages", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reboot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("os", sa.String(64), nullable=False, server_default=""),
        sa.Column("kernel", sa.String(64), nullable=False, server_default=""),
        sa.Column("patched_at", sa.DateTime(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("error", sa.String(300), nullable=False, server_default=""),
        sa.UniqueConstraint("host", "source"),
    )
    op.create_index("ix_host_patch_host", "host_patch", ["host"])


def downgrade() -> None:
    op.drop_index("ix_host_patch_host", "host_patch")
    op.drop_table("host_patch")
