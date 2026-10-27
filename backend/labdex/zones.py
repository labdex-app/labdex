"""Areas and the zones in them.

An area is where something is (DC, CA, DMZ). Every area has a default zone whose code is the
area's own (`DC`): for networks and hosts you haven't classified further. Other zones are
made one by one inside an area (`DC_TC`, `CA_UT`), each with a name, a rank (higher = more
trusted; empty = none) and a colour.

Renaming a code rewrites it on networks, hosts, alias members, policy cells and role scopes;
deleting a zone that's in use is refused.
"""
from __future__ import annotations

import re

from . import naming, store
from .models import AliasGroup, Area, Host, Network, RoleBinding, Zone, ZonePolicy, db

CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")  # an area: no "_", it separates area and zone
SUFFIX_RE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)*$")
ZONE_MAX = 48  # the whole zone code, area included


def clean_code(text, area: bool = False) -> str:
    """What people type becomes a code: "Trusted clients" → TRUSTED_CLIENTS. Spaces, dashes and dots
    turn into "_" (an area drops them: its code can't contain "_"), anything else that isn't a
    letter or digit goes."""
    t = re.sub(r"[\s.\-_]+", "" if area else "_", str(text or "").strip().upper())
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9_]", "", t)).strip("_")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

EXAMPLE_AREAS = naming.DEFAULT_AREAS
# (zone, name, rank, colour, description)
EXAMPLE_ZONES = [
    ("INET_UT", "Internet", 1, "#a3a3ab", "Anything out there"),
    ("CA_TC", "Campus, trusted", 4, "#4ade80", "Managed by me, updated automatically"),
    ("CA_ST", "Campus, semi-trusted", 2, "#60a5fa", "Owner known, managed by someone else"),
    ("CA_UT", "Campus, untrusted (IoT, guests)", 1, "#a3a3ab", "Owner or state unknown"),
    ("RA_ST", "Remote access", 2, "#60a5fa", "VPN clients"),
    ("DC_HT", "Datacenter, highly trusted", 5, "#e879f9", "Management: network gear, hypervisors, BMCs"),
    ("DC_TC", "Datacenter, trusted", 4, "#4ade80", "Servers managed as code"),
    ("DC_TN", "Datacenter, not compliant", 3, "#fbbf24", "Servers that miss part of the baseline"),
    ("DMZ_TC", "DMZ", 4, "#f87171", "Internet-facing"),
]
AREA_COLOURS = {"INET": "#a3a3ab", "CA": "#60a5fa", "RA": "#22d3ee", "DC": "#4ade80", "DMZ": "#f87171"}


def area_dict(a: Area) -> dict:
    return {"code": a.code, "name": a.name, "description": a.description or "", "position": a.position, "colour": a.colour or ""}


def zone_dict(z: Zone) -> dict:
    return {"code": z.code, "area": z.area, "name": z.name, "rank": z.rank, "colour": z.colour or "",
            "description": z.description or "", "default": z.code == z.area}


def overview() -> dict:
    areas = list(db.session.scalars(db.select(Area).order_by(Area.position, Area.code)))
    nets: dict[str, int] = {}
    hosts: dict[str, int] = {}
    for (z,) in db.session.execute(db.select(Network.zone)):
        nets[z] = nets.get(z, 0) + 1
    for (z,) in db.session.execute(db.select(Host.zone)):
        hosts[z] = hosts.get(z, 0) + 1
    zs = []
    for code in naming.all_zones():
        z = db.session.get(Zone, code)
        zs.append(zone_dict(z) | {"uid": z.uid, "long": naming.zone_long(code), "hex": naming.zone_hex(code),
                                  "networks": nets.get(code, 0), "hosts": hosts.get(code, 0)})
    return {"areas": [area_dict(a) for a in areas], "zones": zs, "dmz_area": naming.get_setting("dmz_area")}


# ------------------------------------------------------------------ renames
def _rename_codes(fix) -> None:
    """Apply fix(zone code) -> zone code everywhere a zone is named."""
    for model in (Network, Host):
        for obj in db.session.scalars(db.select(model)):
            obj.zone = fix(obj.zone)
    for al in db.session.scalars(db.select(AliasGroup)):
        members = []
        for m in al.members or []:
            kind, _, val = m.partition(":")
            if kind == "zone":
                m = f"zone:{fix(val)}"
            members.append(m)
        al.members = members
    for c in db.session.scalars(db.select(ZonePolicy)):
        c.src, c.dst = fix(c.src), fix(c.dst)
    for b in db.session.scalars(db.select(RoleBinding)):
        scopes = []
        for sc in b.scopes or []:
            kind, _, val = sc.partition(":")
            scopes.append(f"zone:{fix(val)}" if kind == "zone" and "*" not in val else sc)
        b.scopes = scopes


def _uses(code: str) -> int:
    return (db.session.scalar(db.select(db.func.count()).select_from(Network).where(Network.zone == code)) or 0) + \
        (db.session.scalar(db.select(db.func.count()).select_from(Host).where(Host.zone == code)) or 0)


def _colour(data: dict, errors: list) -> str:
    c = str(data.get("colour") or "").strip()
    if c and not HEX_RE.match(c):
        errors.append({"field": "colour", "message": "a colour like #4ade80"})
    return c.lower()


# -------------------------------------------------------------------- areas
def save_area(data: dict, existing: Area | None = None) -> Area:
    code = clean_code(data.get("code", ""), area=True)
    errors = []
    if not CODE_RE.match(code):
        errors.append({"field": "code", "message": "up to 16 letters or digits, starting with a letter (e.g. DC)"})
    clash = db.session.get(Area, code)
    if clash is not None and clash is not existing:
        errors.append({"field": "code", "message": "already exists"})
    if not str(data.get("name", "")).strip():
        errors.append({"field": "name", "message": "required"})
    colour = _colour(data, errors)
    if errors:
        raise store.ValidationError(errors)
    name = str(data["name"]).strip()[:64]
    if existing is not None and existing.code != code:
        old = existing.code
        a = Area(code=code, position=existing.position, name=name)
        db.session.add(a)
        db.session.flush()
        for z in list(db.session.scalars(db.select(Zone).where(Zone.area == old))):
            new_code = code + z.code[len(old):]
            uid, z.uid = z.uid, f"~{z.uid}"  # the renamed zone keeps its id (and URL)
            db.session.flush()
            db.session.add(Zone(code=new_code, area=code, name=z.name, rank=z.rank, colour=z.colour,
                                description=z.description, position=z.position, uid=uid))
            db.session.delete(z)
        db.session.flush()
        db.session.delete(existing)
        db.session.flush()

        def fix(zc):
            return code + zc[len(old):] if zc == old or zc.startswith(old + "_") else zc
        _rename_codes(fix)
        for al in db.session.scalars(db.select(AliasGroup)):
            al.members = [f"area:{code}" if m == f"area:{old}" else m for m in al.members or []]
        for b in db.session.scalars(db.select(RoleBinding)):
            b.scopes = [f"area:{code}" if sc == f"area:{old}" else sc for sc in b.scopes or []]
        if naming.get_setting("dmz_area") == old:
            store.save_settings({"dmz_area": code})
    else:
        a = existing or Area(code=code, position=_next_position())
        if existing is None:
            db.session.add(a)
    a.name = name
    a.colour = colour
    a.description = str(data.get("description", ""))
    if data.get("position") is not None:
        a.position = int(data["position"])
    db.session.flush()
    ensure_default(a)
    return a


def ensure_default(a: Area) -> Zone:
    """Every area has its default zone (code = the area's), named like the area."""
    z = db.session.get(Zone, a.code)
    if z is None:
        z = Zone(code=a.code, area=a.code, name=a.name, rank=None, colour="", description="", position=0)
        db.session.add(z)
        db.session.flush()
    return z


def _next_position() -> int:
    return (db.session.scalar(db.select(db.func.max(Area.position))) or 0) + 1


def delete_area(code: str) -> None:
    a = db.session.get(Area, code)
    if a is None:
        raise store.ValidationError([{"field": "code", "message": "not found"}])
    zones = list(db.session.scalars(db.select(Zone).where(Zone.area == code)))
    n = sum(_uses(z.code) for z in zones)
    if n:
        raise store.ValidationError([{"field": "code", "message": f"{n} network(s)/host(s) are in {code}'s zones; move them first"}])
    others = [z.code for z in zones if z.code != code]
    if others:
        raise store.ValidationError([{"field": "code", "message": f"delete its zones first: {', '.join(others)}"}])
    for z in zones:
        db.session.delete(z)
    db.session.flush()
    db.session.delete(a)


# -------------------------------------------------------------------- zones
def save_zone(data: dict, existing: Zone | None = None) -> Zone:
    code = clean_code(data.get("code", ""))
    area_code = clean_code(data.get("area") or code.split("_")[0], area=True)
    errors = []
    area = db.session.get(Area, area_code)
    if area is None:
        errors.append({"field": "area", "message": "no such area"})
    is_default = existing is not None and existing.code == existing.area
    if is_default:
        if code != existing.code:
            errors.append({"field": "code", "message": "the area's default zone keeps the area's code; rename the area instead"})
    elif not code.startswith(area_code + "_") or not SUFFIX_RE.match(code[len(area_code) + 1:]) or len(code) > ZONE_MAX:
        errors.append({"field": "code", "message": f"{area_code}_ and a name, e.g. {area_code}_TRUSTED (at most {ZONE_MAX} characters)"})
    elif code.endswith("_DEFAULT"):
        errors.append({"field": "code", "message": "DEFAULT is taken: the area's default zone uses it on the firewall"})
    clash = db.session.get(Zone, code)
    if clash is not None and clash is not existing:
        errors.append({"field": "code", "message": "already exists"})
    rank = data.get("rank")
    if rank in ("", None):
        rank = None
    else:
        try:
            rank = int(rank)
            if not 0 <= rank <= 99:
                raise ValueError
        except (TypeError, ValueError):
            errors.append({"field": "rank", "message": "0–99; higher = more trusted; empty = none"})
            rank = None
    colour = _colour(data, errors)
    if errors:
        raise store.ValidationError(errors)
    name = str(data.get("name") or "").strip()[:64] or code
    if existing is not None and existing.code != code:
        old = existing.code
        uid, existing.uid = existing.uid, f"~{existing.uid}"  # the renamed zone keeps its id (and URL)
        db.session.flush()
        z = Zone(code=code, area=area_code, position=existing.position, uid=uid)
        db.session.add(z)
        db.session.flush()
        _rename_codes(lambda zc: code if zc == old else zc)
        db.session.flush()
        db.session.delete(existing)
    else:
        z = existing or Zone(code=code, area=area_code, position=0)
        if existing is None:
            db.session.add(z)
    z.name, z.rank, z.colour = name, rank, colour
    z.description = str(data.get("description", ""))
    return z


def find_zone(key: str) -> Zone | None:
    """By code or by its stable id."""
    key = str(key or "")
    return db.session.get(Zone, key.upper()) or db.session.scalar(db.select(Zone).where(Zone.uid == key.lower()))


def delete_zone(code: str) -> None:
    z = db.session.get(Zone, code)
    if z is None:
        raise store.ValidationError([{"field": "code", "message": "not found"}])
    if z.code == z.area:
        raise store.ValidationError([{"field": "code", "message": "an area's default zone goes with the area"}])
    n = _uses(code)
    if n:
        raise store.ValidationError([{"field": "code", "message": f"{n} network(s)/host(s) are in {code}; move them first"}])
    cells = db.session.scalar(db.select(db.func.count()).select_from(ZonePolicy).where((ZonePolicy.src == code) | (ZonePolicy.dst == code)))
    if cells:
        raise store.ValidationError([{"field": "code", "message": f"the zone policy has {cells} cell(s) with {code}; clear them first"}])
    db.session.delete(z)


def load_example() -> None:
    if db.session.scalar(db.select(db.func.count()).select_from(Area)):
        raise store.ValidationError([{"field": "example", "message": "only when there are no areas yet"}])
    for i, (code, name) in enumerate(EXAMPLE_AREAS):
        a = Area(code=code, name=name, position=i, colour=AREA_COLOURS.get(code, ""))
        db.session.add(a)
        db.session.flush()
        ensure_default(a)
    for code, name, rank, colour, desc in EXAMPLE_ZONES:
        db.session.add(Zone(code=code, area=code.split("_")[0], name=name, rank=rank, colour=colour, description=desc))


def from_legacy(areas: list[dict], trust_levels: list[dict], used: set[str]) -> list[dict]:
    """Zones for an old file or database that had areas × trust levels: the combinations in use
    (named "<area>, <trust>"), with the trust level's rank, colour and description."""
    an = {str(a["code"]).upper(): a.get("name", a["code"]) for a in areas}
    tl = {str(t["code"]).upper(): t for t in trust_levels}
    out = []
    for code in sorted(used):
        if "_" not in code:
            continue
        a, t = code.rsplit("_", 1)
        if a in an and t in tl:
            tt = tl[t]
            out.append({"code": code, "area": a, "name": f"{an[a]}, {tt.get('name', t)}", "rank": int(tt.get("rank") or 0),
                        "colour": naming.ANSI_HEX.get(tt.get("colour") or naming.TRUST_COLOURS.get(t, ""), ""),
                        "description": tt.get("description", "")})
    return out
