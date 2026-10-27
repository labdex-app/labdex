"""0.10.3: interface names, kinds, a logical parent and secondary addresses; host vendor,
model and icon; links (cables) between ports.

Revision ID: 0019_interfaces
Revises: 0018_tags
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_interfaces"
down_revision = "0018_tags"
branch_labels = None
depends_on = None


def _add(table, cols):
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    cols = [c for c in cols if c.name not in have]
    if cols:
        with op.batch_alter_table(table) as b:
            for c in cols:
                b.add_column(c)


def upgrade() -> None:
    _add("interface", [sa.Column("kind", sa.String(16), nullable=False, server_default=""),
                       sa.Column("parent", sa.String(96), nullable=False, server_default=""),
                       sa.Column("extra_ips", sa.Text(), nullable=False, server_default="[]")])
    _add("host", [sa.Column("vendor", sa.String(100), nullable=False, server_default=""),
                  sa.Column("model", sa.String(100), nullable=False, server_default=""),
                  sa.Column("icon", sa.String(32), nullable=False, server_default="")])
    if "link" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "link",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("a_host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="CASCADE"), nullable=False),
            sa.Column("a_port", sa.String(32), nullable=False),
            sa.Column("b_host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="CASCADE"), nullable=False),
            sa.Column("b_port", sa.String(32), nullable=False),
            sa.Column("medium", sa.String(16), nullable=False, server_default=""),
            sa.Column("speed", sa.String(16), nullable=False, server_default=""),
            sa.Column("notes", sa.String(200), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_table("link")
    with op.batch_alter_table("host") as b:
        for c in ("icon", "model", "vendor"):
            b.drop_column(c)
    with op.batch_alter_table("interface") as b:
        for c in ("extra_ips", "parent", "kind"):
            b.drop_column(c)
