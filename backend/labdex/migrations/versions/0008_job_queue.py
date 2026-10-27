"""0.6.0: jobs are a queue (phase, worker, heartbeat), run history per host, worker processes.

Revision ID: 0008_job_queue
Revises: 0007_service_details
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_job_queue"
down_revision = "0007_service_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("job")}
    cols = [c for c in (sa.Column("phase", sa.String(8), nullable=False, server_default=""),
                        sa.Column("worker", sa.String(128), nullable=True),
                        sa.Column("heartbeat_at", sa.DateTime(), nullable=True)) if c.name not in have]
    if cols:
        with op.batch_alter_table("job") as b:
            for c in cols:
                b.add_column(c)
    if "ix_job_worker" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("job")}:
        op.create_index("ix_job_worker", "job", ["worker"])
    tables = set(insp.get_table_names())
    if "job_host" not in tables:
        op.create_table(
            "job_host",
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("host", sa.String(63), primary_key=True),
        )
        if "ix_job_host_host" not in {i["name"] for i in insp.get_indexes("job_host")}:
            op.create_index("ix_job_host_host", "job_host", ["host"])
        # earlier host-scoped jobs already name their host
        conn = op.get_bind()
        import json
        for jid, params in conn.execute(sa.text("SELECT id, params FROM job")):
            try:
                host = (json.loads(params) if isinstance(params, str) else (params or {})).get("host")
            except (ValueError, AttributeError):
                host = None
            if host:
                conn.execute(sa.text("INSERT INTO job_host (job_id, host) VALUES (:j, :h)"), {"j": jid, "h": str(host)[:63]})
    if "worker" not in tables:
        op.create_table(
            "worker",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("version", sa.String(32), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("seen_at", sa.DateTime(), nullable=False),
            sa.Column("running", sa.Integer(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("worker")
    op.drop_index("ix_job_host_host", "job_host")
    op.drop_table("job_host")
    op.drop_index("ix_job_worker", "job")
    with op.batch_alter_table("job") as b:
        for name in ("heartbeat_at", "worker", "phase"):
            b.drop_column(name)
