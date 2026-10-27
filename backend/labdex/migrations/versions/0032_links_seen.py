"""1.20: links seen by LLDP/CDP and UniFi, and suggestions someone ignored.

Revision ID: 0032_links_seen
Revises: 0031_dashboard_layout
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_links_seen"
down_revision = "0031_dashboard_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "link_seen" not in insp.get_table_names():
        op.create_table(
            "link_seen",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(120), nullable=False, index=True),
            sa.Column("protocol", sa.String(16), nullable=False, server_default=""),
            sa.Column("a_host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="SET NULL"), nullable=True),
            sa.Column("a_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("a_port", sa.String(64), nullable=False, server_default=""),
            sa.Column("b_host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="SET NULL"), nullable=True),
            sa.Column("b_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("b_port", sa.String(64), nullable=False, server_default=""),
            sa.Column("b_mac", sa.String(17), nullable=False, server_default=""),
            sa.Column("b_ip", sa.String(45), nullable=False, server_default=""),
            sa.Column("b_platform", sa.String(120), nullable=False, server_default=""),
            sa.Column("seen_at", sa.DateTime(), nullable=True),
        )
    if "cable_ignore" not in insp.get_table_names():
        op.create_table(
            "cable_ignore",
            sa.Column("key", sa.String(400), primary_key=True),
            sa.Column("by", sa.String(128), nullable=False, server_default=""),
            sa.Column("at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("cable_ignore")
    op.drop_table("link_seen")
