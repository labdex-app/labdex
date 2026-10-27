"""VIP hosts and home zones that follow the primary interface.

- host.kind (device | vip), host.vip_protocol, table vip_member
- host.zone_pinned: existing hosts whose zone differs from their primary
  interface's network keep it (pinned); the rest follow their network from now on.

Revision ID: 0002_vip_zone_follow
Revises: 0001_baseline
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_vip_zone_follow"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: a database adopted from 0.4 may already have these (the old additive upgrade ran first).
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("host")}
    new = [c for c in (sa.Column("zone_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
                       sa.Column("kind", sa.String(length=16), nullable=False, server_default="device"),
                       sa.Column("vip_protocol", sa.String(length=16), nullable=False, server_default=""))
           if c.name not in have]
    if new:
        with op.batch_alter_table("host") as b:
            for c in new:
                b.add_column(c)
    if "vip_member" not in insp.get_table_names():
        op.create_table(
            "vip_member",
            sa.Column("vip_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="CASCADE"), primary_key=True),
        )
    # Pin every host whose zone isn't the zone of the network its primary interface
    # (or, without one, its first interface in a network) sits in.
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT h.id, h.zone, i.id AS iid, i.\"primary\", n.zone AS nzone "
        "FROM host h LEFT JOIN interface i ON i.host_id = h.id LEFT JOIN network n ON n.id = i.network_id "
        "ORDER BY h.id, i.id")).mappings().all()
    derived, zone = {}, {}
    for r in rows:
        zone[r["id"]] = r["zone"]
        if r["nzone"] is None:
            continue
        if r["primary"]:
            derived[r["id"]] = r["nzone"]
        else:
            derived.setdefault(r["id"], r["nzone"])
    # the hostname type for VIPs, on installs whose naming lists already exist
    has_parts = conn.execute(sa.text("SELECT COUNT(*) FROM naming_part")).scalar()
    has_vip = conn.execute(sa.text("SELECT COUNT(*) FROM naming_part WHERE kind = 'type' AND code = 'vip'")).scalar()
    if has_parts and not has_vip:
        pos = conn.execute(sa.text("SELECT COALESCE(MAX(position), 0) + 1 FROM naming_part WHERE kind = 'type'")).scalar()
        conn.execute(sa.text("INSERT INTO naming_part (kind, code, label, description, position) VALUES ('type', 'vip', 'Virtual IP (VIP)', '', :p)"),
                     {"p": pos})
    pin = [hid for hid, z in zone.items() if derived.get(hid) != z]
    for hid in pin:
        conn.execute(sa.text("UPDATE host SET zone_pinned = :t WHERE id = :id"), {"t": True, "id": hid})


def downgrade() -> None:
    op.drop_table("vip_member")
    with op.batch_alter_table("host") as b:
        b.drop_column("vip_protocol")
        b.drop_column("kind")
        b.drop_column("zone_pinned")
