"""1.24: patch panel pass-through ports, switch MAC tables, OIDC sign-in.

Revision ID: 0034_trace_macs_oidc
Revises: 0033_bundled_installed
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_trace_macs_oidc"
down_revision = "0033_bundled_installed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "passthrough" not in {c["name"] for c in insp.get_columns("interface")}:
        with op.batch_alter_table("interface") as b:
            b.add_column(sa.Column("passthrough", sa.String(32), nullable=False, server_default=""))
    if "oidc_subject" not in {c["name"] for c in insp.get_columns("user")}:
        with op.batch_alter_table("user") as b:
            b.add_column(sa.Column("oidc_subject", sa.String(320), nullable=True))
            b.create_unique_constraint("uq_user_oidc_subject", ["oidc_subject"])
    if "mac_seen" not in insp.get_table_names():
        op.create_table(
            "mac_seen",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(120), nullable=False),
            sa.Column("mac", sa.String(17), nullable=False),
            sa.Column("host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="SET NULL"), nullable=True),
            sa.Column("host_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("port", sa.String(64), nullable=False, server_default=""),
            sa.Column("vlan", sa.Integer(), nullable=True),
            sa.Column("ip", sa.String(45), nullable=False, server_default=""),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("seen_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_mac_seen_source", "mac_seen", ["source"])
        op.create_index("ix_mac_seen_mac", "mac_seen", ["mac"])


def downgrade() -> None:
    op.drop_table("mac_seen")
    with op.batch_alter_table("user") as b:
        b.drop_constraint("uq_user_oidc_subject")
        b.drop_column("oidc_subject")
    with op.batch_alter_table("interface") as b:
        b.drop_column("passthrough")
