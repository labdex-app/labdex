"""Sites: home and public parts of one infrastructure, each with its own hosts, networks and integrations.

Everything that exists moves into the site `home`. Integrations stay where they were: they move to
`home` too, except backups, which cover every site.

Revision ID: 0037_sites
Revises: 0036_change_sets
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_sites"
down_revision = "0036_change_sets"
branch_labels = None
depends_on = None

INSTANCE_WIDE = ("backup", "demo-lab")  # plugins that aren't about the hosts of one site


def COLUMNS():
    return [
        ("host", sa.Column("site", sa.String(32), nullable=False, server_default="home")),
        ("network", sa.Column("site", sa.String(32), nullable=False, server_default="home")),
        ("integration", sa.Column("site", sa.String(32), nullable=False, server_default="home")),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "site" not in insp.get_table_names():
        op.create_table(
            "site",
            sa.Column("code", sa.String(32), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("colour", sa.String(7), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
    if not bind.execute(sa.text("SELECT 1 FROM site WHERE code = 'home'")).first():
        bind.execute(sa.text("INSERT INTO site (code, name, description, colour, position) VALUES ('home', 'Home', '', '', 0)"))
    for table, col in COLUMNS():
        if col.name not in {c["name"] for c in insp.get_columns(table)}:
            with op.batch_alter_table(table) as b:
                b.add_column(col)
            if table != "integration":
                op.create_index(f"ix_{table}_site", table, ["site"])
    bind.execute(sa.text("UPDATE integration SET site = '' WHERE plugin_id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
                 {"ids": list(INSTANCE_WIDE)})


def downgrade() -> None:
    for table, col in reversed(COLUMNS()):
        if table != "integration":
            op.drop_index(f"ix_{table}_site", table)
        with op.batch_alter_table(table) as b:
            b.drop_column(col.name)
    op.drop_table("site")
