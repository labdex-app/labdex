"""1.16: what an interface plugs into (connector, speed) and a host's own colour on the map.

Revision ID: 0029_ports
Revises: 0028_vault
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_ports"
down_revision = "0028_vault"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("interface")}
    if "connector" not in cols:
        with op.batch_alter_table("interface") as b:
            b.add_column(sa.Column("connector", sa.String(16), nullable=False, server_default=""))
            b.add_column(sa.Column("speed", sa.String(16), nullable=False, server_default=""))
    if "colour" not in {c["name"] for c in insp.get_columns("host")}:
        with op.batch_alter_table("host") as b:
            b.add_column(sa.Column("colour", sa.String(7), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("host") as b:
        b.drop_column("colour")
    with op.batch_alter_table("interface") as b:
        b.drop_column("speed")
        b.drop_column("connector")
