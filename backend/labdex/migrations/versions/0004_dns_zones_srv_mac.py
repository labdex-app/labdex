"""0.5.3: DNS zones per network and host, SRV per service, where a MAC came from.

- network.dns_zone, host.dns_zone ('' = as before)
- service.publish_srv: new services don't publish DNS-SD records; existing ones keep them
- discovered_host.mac_source

Revision ID: 0004_dns_zones_srv_mac
Revises: 0003_function_dns_pools
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_dns_zones_srv_mac"
down_revision = "0003_function_dns_pools"
branch_labels = None
depends_on = None

def COLUMNS():
    return [
        ("network", sa.Column("dns_zone", sa.String(253), nullable=False, server_default="")),
        ("host", sa.Column("dns_zone", sa.String(253), nullable=False, server_default="")),
        ("service", sa.Column("publish_srv", sa.Boolean(), nullable=False, server_default=sa.false())),
        ("discovered_host", sa.Column("mac_source", sa.String(64), nullable=False, server_default="")),
    ]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    added = set()
    for table, col in COLUMNS():
        if col.name not in {c["name"] for c in insp.get_columns(table)}:
            with op.batch_alter_table(table) as b:
                b.add_column(col)
            added.add((table, col.name))
    if ("service", "publish_srv") in added:
        op.execute(sa.text("UPDATE service SET publish_srv = :t").bindparams(t=True))  # nothing disappears on upgrade
    op.execute("UPDATE discovered_host SET mac_source = source WHERE mac <> '' AND (mac_source IS NULL OR mac_source = '')")


def downgrade() -> None:
    for table, col in reversed(COLUMNS()):
        with op.batch_alter_table(table) as b:
            b.drop_column(col.name)
