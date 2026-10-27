"""Zone codes, naming conventions and derived names.

Every rule from the concept that can be checked mechanically lives here, so the
API, the generators and the linter all agree on what a valid name is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Area, Host, Network, Setting, db
from .models import Zone as ZoneRow

DEFAULT_SETTINGS = {
    "domain": "home.arpa",
    "alias_max_length": "32",
    "alias_ids": "uid",  # uid: ldx_<8 hex> on the firewall; readable: the name if it fits, else shortened
    "categories": "net,srv,dmz,sto,lab,pwr,cli,ext",
    "octet_matches_vlan": "true",
    "dmz_area": "DMZ",
    "instance_name": "labdex",
    "marketplace_urls": "",
    "live_status_minutes": "1440",
    "update_check": "false",  # About page: ask the project's git platform for the latest release
    "project_url": "https://github.com/labdex-app/labdex",  # where labdex lives: GitHub, GitLab, Gitea/Forgejo; help links and the release check
    "update_check_repo": "",  # before 1.24: owner/repo on GitHub (read as https://github.com/<it>)
    "public_url": "",
    "status_stale_minutes": "15",  # a monitoring report older than this counts as unknown  # how people reach labdex, for links in notifications
    "fun_pools_seeded": "false",  # internal: built-in fun name pools were added once
    "official_repo_seeded": "false",  # internal: the official plugin repository was added once
}

DEFAULT_AREAS = [
    ("INET", "Internet"),
    ("CA", "Campus"),
    ("RA", "Remote access"),
    ("DC", "Datacenter"),
    ("DMZ", "Perimeter DMZ"),
]

STATUSES = ["active", "planned", "legacy", "offline"]
ALIAS_TYPES = ["host", "network", "port", "mac", "url", "external"]
ALIAS_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
FUN_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
DNS_NAME_RE = re.compile(r"^(?=.{1,200}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
NET_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

# Prompt / banner colour per trust level (stored on the level); the DMZ area overrides to red.
COLOURS = ["magenta", "green", "yellow", "blue", "cyan", "white", "red"]
TRUST_COLOURS = {"HT": "magenta", "TC": "green", "TN": "yellow", "ST": "blue", "UT": "white"}
ANSI_HEX = {"magenta": "#e879f9", "green": "#4ade80", "yellow": "#fbbf24", "blue": "#60a5fa", "cyan": "#22d3ee",
            "white": "#a3a3ab", "red": "#f87171"}
NEUTRAL_HEX = "#a3a3ab"


def nearest_ansi(hexcol: str) -> str:
    """The prompt colour (an ANSI name) closest to a #rrggbb colour."""
    try:
        r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError, IndexError):
        return "white"

    def dist(name):
        h = ANSI_HEX[name]
        return sum((x - int(h[i:i + 2], 16)) ** 2 for x, i in ((r, 1), (g, 3), (b, 5)))
    return min(ANSI_HEX, key=dist)


# --------------------------------------------------------------------- settings
def _ctx() -> dict:
    """Settings, areas and trust levels, loaded once per inventory revision."""
    from .cache import memo

    def load():
        settings = dict(DEFAULT_SETTINGS)
        settings.update({s.key: s.value for s in db.session.scalars(db.select(Setting))})
        area_rows = list(db.session.scalars(db.select(Area).order_by(Area.position, Area.code)))
        areas = [(a.code, a.name) for a in area_rows]
        order = {a.code: i for i, a in enumerate(area_rows)}
        zone_rows = sorted(db.session.scalars(db.select(ZoneRow)),
                           key=lambda z: (order.get(z.area, 999), z.code != z.area, -(z.rank if z.rank is not None else 999), z.code))
        area_hex = {a.code: a.colour or "" for a in area_rows}
        zones = {z.code: {"area": z.area, "name": z.name or (dict(areas).get(z.area, z.code) if z.code == z.area else z.code),
                          "rank": z.rank, "hex": z.colour or area_hex.get(z.area) or NEUTRAL_HEX}
                 for z in zone_rows if z.area in order}
        from .models import NamingPart
        cats = [p.code for p in db.session.scalars(db.select(NamingPart).where(NamingPart.kind == "category").order_by(NamingPart.position))]
        if not cats:
            cats = [c.strip() for c in settings["categories"].split(",") if c.strip()]
        alt = "|".join(re.escape(c) for c in cats) or "[a-z]+"
        return {"settings": settings, "areas": dict(areas), "area_order": [a[0] for a in areas],
                "zones": zones,
                "categories": cats, "hostname_re": re.compile(rf"^({alt})-[a-z0-9]+-[a-z0-9]+-\d{{2}}$")}
    return memo("naming", load)


def get_setting(key: str) -> str:
    return _ctx()["settings"].get(key, DEFAULT_SETTINGS.get(key, ""))


def settings_dict() -> dict:
    return dict(_ctx()["settings"])


def alias_max() -> int:
    try:
        return int(get_setting("alias_max_length"))
    except ValueError:
        return 32


def categories() -> list[str]:
    return list(_ctx()["categories"])


def hostname_re() -> re.Pattern:
    return _ctx()["hostname_re"]


def ensure_defaults() -> None:
    """Seed settings, areas and trust levels on an empty database."""
    ensure_defaults_in_session()
    db.session.commit()


# defaults that changed: an instance that still has the old one gets the new one
MOVED_DEFAULTS = {"project_url": ("https://github.com/reykimedev/labdex",)}  # 1.24.1: the project moved to labdex-app


def ensure_defaults_in_session() -> None:
    """Only settings get defaults. Areas and trust levels are yours to create
    (Settings → Zones offers the concept's set as a starting point)."""
    for key, value in DEFAULT_SETTINGS.items():
        row = db.session.get(Setting, key)
        if row is None:
            db.session.add(Setting(key=key, value=value))
        elif row.value in MOVED_DEFAULTS.get(key, ()):  # still the old default: follow it, a value you set stays
            row.value = value
    db.session.flush()


# ------------------------------------------------------------------------ zones
@dataclass(frozen=True)
class Zone:
    area: str
    code: str
    rank: int | None = None

    @property
    def trust(self) -> str:
        """The part after the area ('' for the area's default zone)."""
        return self.code[len(self.area) + 1:] if self.code != self.area else ""

    @property
    def default(self) -> bool:
        return self.code == self.area


def parse_zone(code: str) -> Zone | None:
    """'DC_TC' -> Zone('DC', 'DC_TC', 4); None if there is no such zone."""
    z = _ctx()["zones"].get(code or "")
    return Zone(z["area"], code, z["rank"]) if z else None


def all_zones() -> list[str]:
    return list(_ctx()["zones"])


def zone_long(code: str) -> str:
    z = _ctx()["zones"].get(code or "")
    return z["name"] if z else code


def trust_rank(code: str) -> int | None:
    """The zone's rank; None for a zone without one (an area's default) or an unknown zone."""
    z = _ctx()["zones"].get(code or "")
    return z["rank"] if z else None


def zone_hex(code: str) -> str:
    z = _ctx()["zones"].get(code or "")
    return z["hex"] if z else NEUTRAL_HEX


def zone_colour(code: str) -> str:
    """The prompt colour: the ANSI colour nearest to the zone's."""
    return nearest_ansi(zone_hex(code))


# ----------------------------------------------------------------- alias names
def upper(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def network_alias(net: Network, af: str = "") -> str:
    """af: '4', '6' or '' for the dual-stack nested alias."""
    return f"N{af}_ZONE_{net.zone}_{upper(net.name)}"


def host_alias(host: Host, af: str = "") -> str:
    return f"H{af}_ZONE_{host.zone}_{upper(host.name)}"


def firewall_id(name: str, limit: int | None = None) -> str:
    """The alias name on the firewall. With alias_ids = uid (the default since 0.10.5): a short,
    stable uid (ldx_ and 8 hex digits of the name's hash). With readable: the labdex name if it's
    valid there, otherwise a stable shortened form (letters, digits, _; at most `limit`)."""
    import hashlib
    if get_setting("alias_ids") != "readable":
        return "ldx_" + hashlib.sha1(name.encode()).hexdigest()[:8]
    limit = limit or alias_max()
    if ALIAS_NAME_RE.match(name) and len(name) <= limit:
        return name
    base = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", name)).strip("_").upper() or "ALIAS"
    if base[0].isdigit():
        base = "A_" + base
    if len(base) <= limit and base != name:
        return base
    digest = hashlib.sha1(name.encode()).hexdigest()[:5].upper()
    return f"{base[: limit - 6].rstrip('_')}_{digest}"


def zone_group(zone_code: str) -> str:
    """An area's default zone (`DC`) would clash with the whole area's group, so it's NG_ZONE_DC_DEFAULT."""
    return f"NG_ZONE_{zone_code}" if "_" in zone_code else f"NG_ZONE_{zone_code}_DEFAULT"


def area_group(area_code: str) -> str:
    return f"NG_ZONE_{area_code}"


# -------------------------------------------------------------------- DNS names
def fqdn(*labels: str) -> str:
    return ".".join([*labels, get_setting("domain")])


def clean_zone(z: str) -> str:
    return (z or "").strip().lower().rstrip(".")


def network_dns_parent(net) -> str:
    """Where a network's address records live: <host>.<this>.
    dns_zone '' = <network>.<domain>; '@' = the domain itself; else the zone given."""
    z = clean_zone(getattr(net, "dns_zone", "") or "")
    domain = get_setting("domain")
    if not z:
        return f"{net.name}.{domain}"
    return domain if z == "@" else z


def network_dns_zone(net) -> str:
    """The DNS zone a network's records belong to (for syncing): the domain unless overridden."""
    z = clean_zone(getattr(net, "dns_zone", "") or "")
    return z if z and z != "@" else get_setting("domain")


def host_dns_zone(host) -> str:
    """Where a host's short name, fun names, aliases and service names go."""
    return clean_zone(getattr(host, "dns_zone", "") or "") or get_setting("domain")


def home_network(host: Host):
    """The network that gives an unpinned host its zone: the primary interface's, else the first one's."""
    with_net = [i for i in host.interfaces if i.network is not None]
    return next((i.network for i in with_net if i.primary), None) or (with_net[0].network if with_net else None)


def primary_interface(host: Host):
    """The interface the short name and fun name point at."""
    ifaces = [i for i in host.interfaces if i.dns_enabled and i.addresses]
    for i in ifaces:
        if i.primary:
            return i
    return ifaces[0] if ifaces else None
