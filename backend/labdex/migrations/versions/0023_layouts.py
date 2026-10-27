"""1.9: shared layouts of pictures (the topology): where each node sits, the same for everyone.

Revision ID: 0023_layouts
Revises: 0022_service_fqdn
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_layouts"
down_revision = "0022_service_fqdn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "layout" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "layout",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("positions", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("layout")
