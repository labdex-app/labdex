"""SSH login keys through the agent: people's public keys, where each may log in, the break-glass
key per host, and what each agent reported (sshkeys.py).

Revision ID: 0047_ssh_keys
Revises: 0046_agent_ansible
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0047_ssh_keys"
down_revision = "0046_agent_ansible"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}
    with op.batch_alter_table("agent") as b:
        if "ssh_users" not in have:
            b.add_column(sa.Column("ssh_users", sa.Text(), nullable=False, server_default="[]"))
        if "ssh_keys" not in have:
            b.add_column(sa.Column("ssh_keys", sa.Text(), nullable=False, server_default="{}"))
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "ssh_key" not in tables:
        op.create_table(
            "ssh_key",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(80), nullable=False, unique=True),
            sa.Column("owner", sa.String(128), nullable=False, server_default=""),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("sha256", sa.String(60), nullable=False, unique=True),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "ssh_key_grant" not in tables:
        op.create_table(
            "ssh_key_grant",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key_id", sa.Integer(), sa.ForeignKey("ssh_key.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("host", sa.String(63), nullable=False, index=True),
            sa.Column("user", sa.String(32), nullable=False),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("key_id", "host", "user"),
        )
    if "ssh_break_glass" not in tables:
        op.create_table(
            "ssh_break_glass",
            sa.Column("host", sa.String(63), primary_key=True),
            sa.Column("user", sa.String(32), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("sha256", sa.String(60), nullable=False),
            sa.Column("source", sa.String(16), nullable=False, server_default="pasted"),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("ssh_break_glass")
    op.drop_table("ssh_key_grant")
    op.drop_table("ssh_key")
    with op.batch_alter_table("agent") as b:
        b.drop_column("ssh_keys")
        b.drop_column("ssh_users")
