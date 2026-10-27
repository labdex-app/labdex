"""0.10.5: a host's own firewall rules (allow from / to, for the whole device).

Revision ID: 0021_host_rules
Revises: 0020_alias_ids
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_host_rules"
down_revision = "0020_alias_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "rules" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("host")}:
        with op.batch_alter_table("host") as b:
            b.add_column(sa.Column("rules", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("host") as b:
        b.drop_column("rules")
