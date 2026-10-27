"""1.10: zone codes are names, not 8-letter abbreviations: areas up to 16, zones up to 48.

SQLite doesn't enforce VARCHAR lengths, so only other databases need the columns widened.

Revision ID: 0024_long_zone_codes
Revises: 0023_layouts
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_long_zone_codes"
down_revision = "0023_layouts"
branch_labels = None
depends_on = None

COLUMNS = [("area", "code", 16), ("zone", "area", 16), ("zone", "code", 48), ("network", "zone", 48), ("host", "zone", 48),
           ("zone_policy", "src", 48), ("zone_policy", "dst", 48)]


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    for table, column, length in COLUMNS:
        op.alter_column(table, column, type_=sa.String(length), existing_nullable=False)


def downgrade() -> None:
    pass  # longer codes may exist by now; narrowing would cut them
