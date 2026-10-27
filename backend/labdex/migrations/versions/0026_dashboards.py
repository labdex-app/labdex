"""1.12: dashboards, cards on a grid; the start page becomes the shared "Overview" one.

Revision ID: 0026_dashboards
Revises: 0025_uids
Create Date: 2026-10-26
"""
import json
import secrets

from alembic import op
import sqlalchemy as sa

revision = "0026_dashboards"
down_revision = "0025_uids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "dashboard" in sa.inspect(op.get_bind()).get_table_names():
        return
    t = op.create_table(
        "dashboard",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uid", sa.String(12), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_dashboard_owner", "dashboard", ["owner"])
    # the start page as it was, as cards (kept here as written: the migration mustn't change later)
    DEFAULT = [
        {"id": "counts", "type": "counts", "x": 0, "y": 0, "w": 12, "h": 3, "config": {}},
        {"id": "widgets", "type": "widgets", "x": 0, "y": 3, "w": 12, "h": 6, "config": {}},
        {"id": "approvals", "type": "approvals", "x": 0, "y": 9, "w": 7, "h": 6, "config": {}},
        {"id": "changes", "type": "changes", "x": 7, "y": 9, "w": 5, "h": 8, "config": {}},
        {"id": "failing", "type": "failing", "x": 0, "y": 15, "w": 7, "h": 5, "config": {}},
        {"id": "upcoming", "type": "upcoming", "x": 7, "y": 17, "w": 5, "h": 4, "config": {}},
        {"id": "running", "type": "running", "x": 0, "y": 20, "w": 7, "h": 4, "config": {}},
        {"id": "zones", "type": "zones", "x": 7, "y": 21, "w": 5, "h": 3, "config": {}},
    ]
    uid = "".join(secrets.choice("23456789abcdefghjkmnpqrstuvwxyz") for _ in range(10))
    op.bulk_insert(t, [{"uid": uid, "name": "Overview", "owner": "", "position": 0, "items": json.dumps(DEFAULT), "updated_by": "labdex"}])


def downgrade() -> None:
    op.drop_table("dashboard")
