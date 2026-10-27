"""0.9.2: public services (port forward, split-horizon DNS).

Revision ID: 0015_public_services
Revises: 0014_zone_policy
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_public_services"
down_revision = "0014_zone_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("service")}
    cols = [c for c in (sa.Column("public", sa.Boolean(), nullable=False, server_default=sa.false()),
                        sa.Column("public_port", sa.Integer(), nullable=True),
                        sa.Column("public_name", sa.String(253), nullable=False, server_default="")) if c.name not in have]
    if cols:
        with op.batch_alter_table("service") as b:
            for c in cols:
                b.add_column(c)


def downgrade() -> None:
    with op.batch_alter_table("service") as b:
        for name in ("public_name", "public_port", "public"):
            b.drop_column(name)
