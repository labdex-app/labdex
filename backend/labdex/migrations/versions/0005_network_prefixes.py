"""0.5.5: several prefixes per network (IPv4 and IPv6), ready for prefix delegation.

The old single-prefix columns stay and mirror the first prefix of each family.

Revision ID: 0005_network_prefixes
Revises: 0004_dns_zones_srv_mac
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_network_prefixes"
down_revision = "0004_dns_zones_srv_mac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "network_prefix" not in insp.get_table_names():
        op.create_table(
            "network_prefix",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("network_id", sa.Integer(), sa.ForeignKey("network.id", ondelete="CASCADE"), nullable=False),
            sa.Column("family", sa.Integer(), nullable=False, server_default="4"),
            sa.Column("prefix", sa.String(49), nullable=False, server_default=""),
            sa.Column("source", sa.String(16), nullable=False, server_default="static"),
            sa.Column("delegated_from", sa.String(32), nullable=False, server_default=""),
            sa.Column("prefix_id", sa.Integer(), nullable=True),
            sa.Column("length", sa.Integer(), nullable=True),
            sa.Column("gateway", sa.String(45), nullable=False, server_default=""),
            sa.Column("dhcp_start", sa.String(45), nullable=False, server_default=""),
            sa.Column("dhcp_end", sa.String(45), nullable=False, server_default=""),
            sa.Column("description", sa.String(64), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT COUNT(*) FROM network_prefix")).scalar():
        return
    ins = sa.text("INSERT INTO network_prefix (network_id, family, prefix, gateway, dhcp_start, dhcp_end, position) "
                  "VALUES (:n, :f, :p, :g, :ds, :de, :pos)")
    for r in conn.execute(sa.text("SELECT id, prefix4, prefix6, gateway4, dhcp_start, dhcp_end FROM network")).mappings():
        pos = 0
        if r["prefix4"]:
            conn.execute(ins, {"n": r["id"], "f": 4, "p": r["prefix4"], "g": r["gateway4"] or "", "ds": r["dhcp_start"] or "",
                               "de": r["dhcp_end"] or "", "pos": pos})
            pos += 1
        if r["prefix6"]:
            conn.execute(ins, {"n": r["id"], "f": 6, "p": r["prefix6"], "g": "", "ds": "", "de": "", "pos": pos})


def downgrade() -> None:
    op.drop_table("network_prefix")
