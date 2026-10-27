"""0.5.9: services get a description, a bind address and a link mode.

Revision ID: 0007_service_details
Revises: 0006_ipv6_tokens
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_service_details"
down_revision = "0006_ipv6_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("service")}
    cols = [c for c in (sa.Column("description", sa.String(200), nullable=False, server_default=""),
                        sa.Column("bind", sa.String(64), nullable=False, server_default=""),
                        sa.Column("link_to", sa.String(8), nullable=False, server_default="")) if c.name not in have]
    if cols:
        with op.batch_alter_table("service") as b:
            for c in cols:
                b.add_column(c)


def downgrade() -> None:
    with op.batch_alter_table("service") as b:
        for name in ("link_to", "bind", "description"):
            b.drop_column(name)
