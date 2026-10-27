"""0.10.1: zones are made one by one inside an area, and every area has a default zone.

Before, a zone was any area × trust level. Now there is a `zone` table: each area gets its
default zone (code = the area's), and every <area>_<trust> combination that is in use
(networks, hosts, zone policy, alias members, role scopes) becomes a zone named
"<area>, <trust>" with the trust level's rank, colour and description. Trust levels go.

Revision ID: 0017_zones
Revises: 0016_scan_macs
Create Date: 2026-10-25
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0017_zones"
down_revision = "0016_scan_macs"
branch_labels = None
depends_on = None

ANSI_HEX = {"magenta": "#e879f9", "green": "#4ade80", "yellow": "#fbbf24", "blue": "#60a5fa", "cyan": "#22d3ee",
            "white": "#a3a3ab", "red": "#f87171"}
TRUST_COLOURS = {"HT": "magenta", "TC": "green", "TN": "yellow", "ST": "blue", "UT": "white"}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "colour" not in {c["name"] for c in insp.get_columns("area")}:
        with op.batch_alter_table("area") as b:
            b.add_column(sa.Column("colour", sa.String(7), nullable=False, server_default=""))
    if "zone" not in tables:
        op.create_table(
            "zone",
            sa.Column("code", sa.String(17), primary_key=True),
            sa.Column("area", sa.String(8), sa.ForeignKey("area.code"), nullable=False),
            sa.Column("name", sa.String(64), nullable=False, server_default=""),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("colour", sa.String(7), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
    have = {r[0] for r in bind.execute(sa.text("SELECT code FROM zone"))}
    areas = {r[0]: r[1] for r in bind.execute(sa.text("SELECT code, name FROM area"))}
    trusts = {}
    if "trust_level" in tables:
        for code, name, rank, desc, colour in bind.execute(sa.text("SELECT code, name, rank, description, colour FROM trust_level")):
            trusts[code] = (name, rank, desc or "", ANSI_HEX.get(colour or TRUST_COLOURS.get(code, ""), ""))

    used: set[str] = set()
    for table in ("network", "host"):
        used |= {r[0] for r in bind.execute(sa.text(f"SELECT zone FROM {table}")) if r[0]}
    if "zone_policy" in tables:
        for src, dst in bind.execute(sa.text("SELECT src, dst FROM zone_policy")):
            used |= {src, dst}
    for (members,) in bind.execute(sa.text("SELECT members FROM alias_group")):
        for m in json.loads(members or "[]"):
            if str(m).startswith("zone:"):
                used.add(str(m)[5:])
    for (scopes,) in bind.execute(sa.text("SELECT scopes FROM role_binding")):
        for sc in json.loads(scopes or "[]"):
            if str(sc).startswith("zone:") and "*" not in sc:
                used.add(str(sc)[5:])

    ins = sa.text("INSERT INTO zone (code, area, name, rank, colour, description, position) VALUES (:c, :a, :n, :r, :col, :d, :p)")
    for code, name in areas.items():
        if code not in have:
            bind.execute(ins, {"c": code, "a": code, "n": name, "r": None, "col": "", "d": "", "p": 0})
            have.add(code)
    for code in sorted(used):
        if code in have or "_" not in code:
            continue
        a, t = code.rsplit("_", 1)
        if a in areas and t in trusts:
            name, rank, desc, colour = trusts[t]
            bind.execute(ins, {"c": code, "a": a, "n": f"{areas[a]}, {name}"[:64], "r": rank, "col": colour, "d": desc, "p": 0})
            have.add(code)
    if "trust_level" in tables:
        op.drop_table("trust_level")


def downgrade() -> None:
    op.create_table(
        "trust_level",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("colour", sa.String(16), nullable=False, server_default=""),
    )
    bind = op.get_bind()
    seen = set()
    for code, area, _name, rank, desc in bind.execute(sa.text("SELECT code, area, name, rank, description FROM zone ORDER BY code")):
        t = code[len(area) + 1:] if code != area else ""
        if t and t not in seen:
            seen.add(t)
            bind.execute(sa.text("INSERT INTO trust_level (code, name, rank, description, colour) VALUES (:c, :n, :r, :d, '')"),
                         {"c": t, "n": t, "r": rank or 0, "d": desc or ""})
    op.drop_table("zone")
    with op.batch_alter_table("area") as b:
        b.drop_column("colour")
