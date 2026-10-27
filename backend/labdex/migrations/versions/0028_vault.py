"""1.15: the KV vault: entries with encrypted values, their versions, and the wrapped master key.

Revision ID: 0028_vault
Revises: 0027_racks
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_vault"
down_revision = "0027_racks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "vault_key" not in have:
        op.create_table(
            "vault_key",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("salt", sa.String(64), nullable=False),
            sa.Column("kdf", sa.String(64), nullable=False),
            sa.Column("wrapped", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("rotated_at", sa.DateTime(), nullable=True),
        )
    if "vault_entry" not in have:
        op.create_table(
            "vault_entry",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uid", sa.String(12), nullable=False, unique=True),
            sa.Column("path", sa.String(200), nullable=False, unique=True),
            sa.Column("host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("fields", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "vault_version" not in have:
        op.create_table(
            "vault_version",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("entry_id", sa.Integer(), sa.ForeignKey("vault_entry.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("wrapped_key", sa.Text(), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("fields", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("entry_id", "version"),
        )


def downgrade() -> None:
    op.drop_table("vault_version")
    op.drop_table("vault_entry")
    op.drop_table("vault_key")
