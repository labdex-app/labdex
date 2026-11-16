"""Sites: the parts of one infrastructure (`home`, `public`), each with its own hosts, networks,
integrations and credentials. Zones, naming rules, features, users and notification channels are
shared by every site.

Everything derived from the inventory (DNS records, firewall aliases, the Ansible inventory, DHCP)
can be built for one site: inside `scoped("home")` the generators only see that site's hosts and
networks. Integrations run scoped to their site, so the home firewall never gets public nodes and a
public DNS provider never gets home records, unless a custom alias names them on purpose.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar

from .models import DEFAULT_SITE, Host, Integration, Network, Site, db

SITE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
COLOUR_RE = re.compile(r"^#[0-9a-f]{6}$")

_scope: ContextVar[str | None] = ContextVar("labdex_site_scope", default=None)


# ------------------------------------------------------------------- scope
@contextmanager
def scoped(site: str | None):
    """Generators inside see only `site` ('' or None = every site)."""
    token = _scope.set(site or None)
    try:
        yield
    finally:
        _scope.reset(token)


def current() -> str | None:
    """The site the generators are limited to right now (None = every site)."""
    return _scope.get()


def hosts(q):
    """Limit a select(Host) to the current scope."""
    s = current()
    return q.where(Host.site == s) if s else q


def networks(q):
    s = current()
    return q.where(Network.site == s) if s else q


def in_scope(site: str) -> bool:
    s = current()
    return not s or site == s


# ------------------------------------------------------------------- sites
def all_sites() -> list[Site]:
    return list(db.session.scalars(db.select(Site).order_by(Site.position, Site.code)))


def codes() -> list[str]:
    return [s.code for s in all_sites()]


def default() -> str:
    """Where new hosts and networks go when nothing says otherwise: the first site."""
    first = db.session.scalar(db.select(Site.code).order_by(Site.position, Site.code).limit(1))
    return first or DEFAULT_SITE


def ensure_default() -> None:
    """Every inventory has at least one site."""
    if db.session.scalar(db.select(db.func.count()).select_from(Site)) == 0:
        db.session.add(Site(code=DEFAULT_SITE, name="Home", position=0))
        db.session.flush()


def exists(code: str) -> bool:
    return db.session.get(Site, code) is not None


def resolve(value, fallback: str | None = None) -> str:
    """The site a host or network names, or `fallback` (default: the first site)."""
    v = str(value or "").strip().lower()
    return v or fallback or default()


def usage(code: str) -> dict:
    count = lambda m, col: db.session.scalar(db.select(db.func.count()).select_from(m).where(col == code)) or 0
    return {"hosts": count(Host, Host.site), "networks": count(Network, Network.site), "integrations": count(Integration, Integration.site)}


def to_dict(s: Site, with_usage: bool = False) -> dict:
    d = {"code": s.code, "name": s.name or s.code, "description": s.description or "", "colour": s.colour or ""}
    if with_usage:
        d["usage"] = usage(s.code)
    return d


def save(data: dict, existing: Site | None = None) -> Site:
    from .store import ValidationError
    errors = []
    code = str(data.get("code") or (existing.code if existing else "")).strip().lower()
    if existing is not None and code != existing.code:
        errors.append({"field": "code", "message": "a site's code can't change; make a new site and move hosts there"})
    elif existing is None:
        if not SITE_RE.match(code):
            errors.append({"field": "code", "message": "lowercase letters, digits and dashes, starting with a letter (max 32)"})
        elif exists(code):
            errors.append({"field": "code", "message": f"site '{code}' already exists"})
    colour = str(data.get("colour") or "").strip().lower()
    if colour and not COLOUR_RE.match(colour):
        errors.append({"field": "colour", "message": "#rrggbb, or empty"})
    if errors:
        raise ValidationError(errors)
    s = existing or Site(code=code, position=(db.session.scalar(db.select(db.func.max(Site.position))) or 0) + 1)
    s.name = str(data.get("name") or "").strip()[:64] or code.capitalize()
    s.description = str(data.get("description") or "")
    s.colour = colour
    if existing is None:
        db.session.add(s)
    db.session.flush()
    return s


def delete(s: Site) -> None:
    """Only an empty site goes, and never the last one."""
    from .store import ValidationError
    used = usage(s.code)
    if any(used.values()):
        what = ", ".join(f"{n} {k}" for k, n in used.items() if n)
        raise ValidationError([{"field": "code", "message": f"site '{s.code}' still has {what}; move or delete them first"}])
    if len(all_sites()) <= 1:
        raise ValidationError([{"field": "code", "message": "the last site can't be deleted"}])
    db.session.delete(s)
    db.session.flush()


def reorder(order: list[str]) -> None:
    pos = {c: i for i, c in enumerate(order)}
    for s in all_sites():
        s.position = pos.get(s.code, len(pos) + s.position)
    db.session.flush()
