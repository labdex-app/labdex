"""1.1: a service's DNS name may be a full name (grafana.example.net.), so it gets longer.

Revision ID: 0022_service_fqdn
Revises: 0021_host_rules
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_service_fqdn"
down_revision = "0021_host_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("service") as b:
        b.alter_column("dns_name", type_=sa.String(253), existing_type=sa.String(63), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("service") as b:
        b.alter_column("dns_name", type_=sa.String(63), existing_type=sa.String(253), existing_nullable=False)
