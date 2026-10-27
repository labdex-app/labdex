"""1.21: not every plugin that ships with labdex is installed. A new labdex starts with the core
set; an existing one keeps the core set and every plugin it has an instance of.

Revision ID: 0033_bundled_installed
Revises: 0032_links_seen
Create Date: 2026-10-27
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0033_bundled_installed"
down_revision = "0032_links_seen"
branch_labels = None
depends_on = None

CORE = ["backup", "exports", "ansible", "nmap"]


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT 1 FROM setting WHERE key = 'bundled_installed'")).first():
        return
    used = [r[0] for r in conn.execute(sa.text("SELECT DISTINCT plugin_id FROM integration"))]
    conn.execute(sa.text("INSERT INTO setting (key, value) VALUES ('bundled_installed', :v)"),
                 {"v": json.dumps(sorted(set(CORE) | set(used)))})


def downgrade() -> None:
    op.execute("DELETE FROM setting WHERE key = 'bundled_installed'")
