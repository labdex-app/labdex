"""0.5.6: an IPv6 interface ID (token) per interface, used in every IPv6 prefix, and per-prefix overrides.

Existing explicit IPv6 addresses stay as they are.

Revision ID: 0006_ipv6_tokens
Revises: 0005_network_prefixes
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_ipv6_tokens"
down_revision = "0005_network_prefixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("interface")}
    cols = [c for c in (sa.Column("ip6_token", sa.String(45), nullable=False, server_default=""),
                        sa.Column("ip6_overrides", sa.Text(), nullable=True)) if c.name not in have]
    if cols:
        with op.batch_alter_table("interface") as b:
            for c in cols:
                b.add_column(c)


def downgrade() -> None:
    with op.batch_alter_table("interface") as b:
        b.drop_column("ip6_overrides")
        b.drop_column("ip6_token")
