"""1.14: racks, where a host sits in one, and what else fills them (patch panels, blanks …).

Revision ID: 0027_racks
Revises: 0026_dashboards
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_racks"
down_revision = "0026_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "rack" not in insp.get_table_names():
        op.create_table(
            "rack",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uid", sa.String(12), nullable=False, unique=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("site", sa.String(64), nullable=False, server_default=""),
            sa.Column("units", sa.Integer(), nullable=False, server_default="42"),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
    if "rack_item" not in insp.get_table_names():
        op.create_table(
            "rack_item",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rack_id", sa.Integer(), sa.ForeignKey("rack.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("kind", sa.String(16), nullable=False, server_default="blank"),
            sa.Column("label", sa.String(64), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("unit", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("face", sa.String(8), nullable=False, server_default="front"),
        )
    if "rack_id" not in {c["name"] for c in insp.get_columns("host")}:
        with op.batch_alter_table("host") as b:
            b.add_column(sa.Column("rack_id", sa.Integer(), nullable=True))
            b.add_column(sa.Column("rack_unit", sa.Integer(), nullable=True))
            b.add_column(sa.Column("rack_height", sa.Integer(), nullable=False, server_default="1"))
            b.add_column(sa.Column("rack_face", sa.String(8), nullable=False, server_default="front"))
            b.create_foreign_key("fk_host_rack", "rack", ["rack_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("host") as b:
        b.drop_constraint("fk_host_rack", type_="foreignkey")
        for c in ("rack_face", "rack_height", "rack_unit", "rack_id"):
            b.drop_column(c)
    op.drop_table("rack_item")
    op.drop_table("rack")
