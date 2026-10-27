"""1.19: a tighter default Overview dashboard (no empty stretches), for whoever never changed it.

Revision ID: 0031_dashboard_layout
Revises: 0030_cable_labels
Create Date: 2026-10-27
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0031_dashboard_layout"
down_revision = "0030_cable_labels"
branch_labels = None
depends_on = None

OLD = [  # as 0026 wrote it
    {"id": "counts", "type": "counts", "x": 0, "y": 0, "w": 12, "h": 3, "config": {}},
    {"id": "widgets", "type": "widgets", "x": 0, "y": 3, "w": 12, "h": 6, "config": {}},
    {"id": "approvals", "type": "approvals", "x": 0, "y": 9, "w": 7, "h": 6, "config": {}},
    {"id": "changes", "type": "changes", "x": 7, "y": 9, "w": 5, "h": 8, "config": {}},
    {"id": "failing", "type": "failing", "x": 0, "y": 15, "w": 7, "h": 5, "config": {}},
    {"id": "upcoming", "type": "upcoming", "x": 7, "y": 17, "w": 5, "h": 4, "config": {}},
    {"id": "running", "type": "running", "x": 0, "y": 20, "w": 7, "h": 4, "config": {}},
    {"id": "zones", "type": "zones", "x": 7, "y": 21, "w": 5, "h": 3, "config": {}},
]
NEW = [
    {"id": "counts", "type": "counts", "x": 0, "y": 0, "w": 12, "h": 3, "config": {}},
    {"id": "widgets", "type": "widgets", "x": 0, "y": 3, "w": 12, "h": 3, "config": {}},
    {"id": "approvals", "type": "approvals", "x": 0, "y": 6, "w": 7, "h": 3, "config": {}},
    {"id": "failing", "type": "failing", "x": 0, "y": 9, "w": 7, "h": 3, "config": {}},
    {"id": "running", "type": "running", "x": 0, "y": 12, "w": 7, "h": 3, "config": {}},
    {"id": "upcoming", "type": "upcoming", "x": 0, "y": 15, "w": 7, "h": 3, "config": {}},
    {"id": "changes", "type": "changes", "x": 7, "y": 6, "w": 5, "h": 9, "config": {}},
    {"id": "zones", "type": "zones", "x": 7, "y": 15, "w": 5, "h": 3, "config": {}},
]


def _key(items):
    return sorted(json.dumps(i, sort_keys=True) for i in items)


def upgrade() -> None:
    conn = op.get_bind()
    for did, items in conn.execute(sa.text("SELECT id, items FROM dashboard WHERE owner = '' AND name = 'Overview'")).fetchall():
        try:
            same = _key(json.loads(items or "[]")) == _key(OLD)
        except ValueError:
            continue
        if same:
            conn.execute(sa.text("UPDATE dashboard SET items = :i WHERE id = :d"), {"i": json.dumps(NEW), "d": did})


def downgrade() -> None:
    pass
