"""Git source per site: where a site's file lives in git, and how far labdex has synced it.

Revision ID: 0038_site_git
Revises: 0037_sites
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_site_git"
down_revision = "0037_sites"
branch_labels = None
depends_on = None


def COLUMNS():
    return [sa.Column("git", sa.Text(), nullable=True), sa.Column("git_state", sa.Text(), nullable=True)]


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("site")}
    for col in COLUMNS():
        if col.name not in have:
            with op.batch_alter_table("site") as b:
                b.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table("site") as b:
        for col in reversed(COLUMNS()):
            b.drop_column(col.name)
