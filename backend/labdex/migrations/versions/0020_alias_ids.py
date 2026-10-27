"""0.10.5: firewall aliases get short uids (ldx_3fa9c201) by default. An instance that
already has an inventory keeps its readable ids until you switch, so nothing on the firewall
is renamed by an upgrade.

Revision ID: 0020_alias_ids
Revises: 0019_interfaces
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_alias_ids"
down_revision = "0019_interfaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    has_inventory = bind.execute(sa.text("SELECT COUNT(*) FROM host")).scalar() or bind.execute(sa.text("SELECT COUNT(*) FROM network")).scalar()
    have = bind.execute(sa.text("SELECT COUNT(*) FROM setting WHERE key = 'alias_ids'")).scalar()
    if has_inventory and not have:
        bind.execute(sa.text("INSERT INTO setting (key, value) VALUES ('alias_ids', 'readable')"))


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM setting WHERE key = 'alias_ids'"))
