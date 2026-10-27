"""0.10.0: MACs come from ARP/NDP and DHCP, not from scans. Forget the ones scans learned
(nmap sees the next router's MAC, or a container bridge's).

Revision ID: 0016_scan_macs
Revises: 0015_public_services
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_scan_macs"
down_revision = "0015_public_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE discovered_host SET mac = '', mac_source = '' WHERE mac_source LIKE 'nmap:%' OR mac_source LIKE 'arp:%'"))


def downgrade() -> None:
    pass
