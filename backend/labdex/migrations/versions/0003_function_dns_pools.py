"""0.5.2: host.role → host.function, per-host DNS records in core, fun name pools.

- host.role is renamed to host.function (the hostname's role part is unrelated)
- host.fun_no_dns: fun names that get no DNS record
- host_record: extra DNS records per host; the Technitium module's per-host
  "records" setting moves here (and is removed from the module)
- fun_pool: lists of fun names to suggest from

Revision ID: 0003_function_dns_pools
Revises: 0002_vip_zone_follow
Create Date: 2026-10-24
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0003_function_dns_pools"
down_revision = "0002_vip_zone_follow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("host")}
    if "role" in cols and "function" in cols:
        # adopted from 0.4: the additive upgrade already added an empty 'function'
        op.execute("UPDATE host SET function = role WHERE (function IS NULL OR function = '') AND role IS NOT NULL")
    with op.batch_alter_table("host") as b:
        if "role" in cols and "function" not in cols:
            b.alter_column("role", new_column_name="function", existing_type=sa.String(200), existing_nullable=False)
        elif "role" in cols:
            b.drop_column("role")
        if "fun_no_dns" not in cols:
            b.add_column(sa.Column("fun_no_dns", sa.Text(), nullable=False, server_default="[]"))
    tables = set(insp.get_table_names())
    if "host_record" not in tables:
        op.create_table(
            "host_record",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("host_id", sa.Integer(), sa.ForeignKey("host.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(253), nullable=False, server_default=""),
            sa.Column("type", sa.String(8), nullable=False),
            sa.Column("value", sa.String(1024), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
    if "fun_pool" not in tables:
        op.create_table(
            "fun_pool",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("names", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("applies_to", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    # Technitium per-host records → host_record
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT hm.id, hm.host_name, hm.settings FROM host_module hm JOIN integration i ON i.id = hm.integration_id "
        "WHERE i.plugin_id = 'technitium'")).mappings().all()
    for r in rows:
        try:
            settings = json.loads(r["settings"] or "{}") or {}
        except ValueError:
            continue
        recs = settings.pop("records", None) or []
        if not recs:
            continue
        host_id = conn.execute(sa.text("SELECT id FROM host WHERE name = :n"), {"n": r["host_name"]}).scalar()
        if host_id is not None:
            have = {(x[0], x[1], x[2]) for x in conn.execute(sa.text(
                "SELECT name, type, value FROM host_record WHERE host_id = :h"), {"h": host_id})}
            pos = len(have)
            for rec in recs:
                key = (str(rec.get("name") or ""), str(rec.get("type") or "").upper(), str(rec.get("value") or ""))
                if not key[1] or key in have:
                    continue
                conn.execute(sa.text("INSERT INTO host_record (host_id, name, type, value, position) VALUES (:h, :n, :t, :v, :p)"),
                             {"h": host_id, "n": key[0], "t": key[1], "v": key[2], "p": pos})
                have.add(key)
                pos += 1
        conn.execute(sa.text("UPDATE host_module SET settings = :s WHERE id = :id"), {"s": json.dumps(settings), "id": r["id"]})


def downgrade() -> None:
    op.drop_table("fun_pool")
    op.drop_table("host_record")
    with op.batch_alter_table("host") as b:
        b.drop_column("fun_no_dns")
        b.alter_column("function", new_column_name="role", existing_type=sa.String(200), existing_nullable=False)
