"""Role-based access control: scoped role bindings plus per-object grants.

A permission is "<object type>:<action>", with "*" as wildcard on either side.
Actions: view < edit; apply and admin are separate. A role binding gives a role
to a user or a group, limited to scopes:

    *               everything
    zone:DC_*       objects in matching zones (glob)
    area:CA         objects in an area
    network:main    hosts with an interface in main, the network itself
    tag:admin       hosts with the tag
    name:srv-*      objects whose name (or fun name) matches

Object grants add permissions on single objects (name globs allowed).
Admins (User.is_admin) can do everything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from flask import abort, g

from . import naming
from .models import ObjectGrant, Role, RoleBinding, User, db

OBJECT_TYPES = ["host", "network", "alias", "integration", "discovery", "audit", "secret"]
# vault secrets: "*" in a permission or grant covers seeing their names, never revealing or changing
# them; that takes secret:apply (reveal) or secret:edit (change) by name
GUARDED = {"secret"}
ACTIONS = ["view", "edit", "apply", "admin"]
IMPLIES = {"view": {"view"}, "edit": {"edit", "view"}, "apply": {"apply", "view"}, "admin": set(ACTIONS)}

BUILTIN_ROLES = [
    ("viewer", "Read everything in scope", ["*:view"]),
    ("editor", "Edit inventory objects in scope", ["host:edit", "network:edit", "alias:edit", "discovery:view", "audit:view", "integration:view"]),
    ("operator", "Run integration actions and apply plans, also per host", ["*:view", "integration:apply", "host:apply"]),
    ("integration-admin", "Configure integrations", ["integration:admin", "*:view"]),
    ("admin", "Everything in scope (vault secrets: names only)", ["*:admin"]),
    ("secret-keeper", "See, reveal and change vault secrets in scope", ["secret:edit", "secret:apply"]),
]

SCOPE_RE = re.compile(r"^(\*|(zone|area|network|tag|name):[A-Za-z0-9_*?.\-\[\]/]+)$")  # "/": vault paths


@dataclass
class Facts:
    """What a scope can match against."""

    name: str = ""
    names: set = field(default_factory=set)  # name + fun name etc.
    zones: set = field(default_factory=set)
    areas: set = field(default_factory=set)
    networks: set = field(default_factory=set)
    tags: set = field(default_factory=set)
    # zones of a host's addresses besides its home zone: zone/area scopes match them for "view" only
    seen_zones: set = field(default_factory=set)
    seen_areas: set = field(default_factory=set)

    @classmethod
    def make(cls, name="", zones=(), networks=(), tags=(), aliases=()):
        zones = {z for z in zones if z}
        return cls(
            name=name,
            names={n for n in (name, *aliases) if n},
            zones=zones,
            areas={z.split("_")[0] for z in zones},
            networks={n for n in networks if n},
            tags=set(tags),
        )


# --------------------------------------------------------------------- facts
def host_facts(d: dict) -> Facts:
    f = Facts.make(d.get("name", ""), [d.get("zone", "")],
                   [i.get("network") for i in d.get("interfaces") or []],
                   d.get("tags") or [], d.get("fun_names") or [d.get("fun") or ""])
    f.seen_zones = {i["zone"] for i in d.get("interfaces") or [] if i.get("zone")} - f.zones
    f.seen_areas = {z.split("_")[0] for z in f.seen_zones} - f.areas
    return f


def network_facts(d: dict) -> Facts:
    return Facts.make(d.get("name", ""), [d.get("zone", "")], [d.get("name", "")])


ALIAS_ZONE = re.compile(r"^[A-Z]+[46]?_ZONE_([A-Z0-9_]+)$")


def alias_facts(name: str) -> Facts:
    """Generated aliases carry their zone: N_ZONE_DC_TC_SRV, H_ZONE_DC_WEB_01 (the DC default
    zone), NG_ZONE_DC_DEFAULT; NG_ZONE_DC is the whole area."""
    m = ALIAS_ZONE.match(name)
    if not m:
        return Facts.make(name)
    parts = m.group(1).split("_")
    if len(parts) == 1:  # whole-area group: matches area scopes only
        f = Facts.make(name)
        f.areas = {parts[0]}
        return f
    if len(parts) == 2 and parts[1] == "DEFAULT" and naming.parse_zone(parts[0]):
        return Facts.make(name, [parts[0]])
    for k in (2, 1):
        cand = "_".join(parts[:k])
        if naming.parse_zone(cand):
            return Facts.make(name, [cand])
    return Facts.make(name)


def named_facts(name: str) -> Facts:
    return Facts.make(name)


def facts_for(object_type: str, snapshot: dict | None, name: str) -> Facts:
    """Facts for an audit snapshot or a plain name."""
    snap = snapshot or {}
    if object_type == "host":
        return host_facts({"name": name, **snap})
    if object_type == "network":
        return network_facts({"name": name, **snap})
    if object_type == "alias":
        return alias_facts(name)
    return named_facts(name)


# ----------------------------------------------------------------- matching
def scope_matches(scope: str, f: Facts, action: str = "") -> bool:
    if scope == "*":
        return True
    kind, _, pattern = scope.partition(":")
    pool = {
        "zone": f.zones, "area": f.areas, "network": f.networks, "tag": f.tags, "name": f.names,
    }.get(kind, set())
    if action == "view" and kind in ("zone", "area"):
        # a host with an address in a zone is visible to whoever may see that zone
        pool = pool | (f.seen_zones if kind == "zone" else f.seen_areas)
    return any(fnmatchcase(v, pattern) for v in pool)


def _grant_type_ok(gtype: str, object_type: str, action: str) -> bool:
    return gtype == object_type or (gtype == "*" and not (object_type in GUARDED and action != "view"))


def perm_matches(perm: str, object_type: str, action: str) -> bool:
    ptype, _, paction = perm.partition(":")
    if ptype not in ("*", object_type):
        return False
    if ptype == "*" and object_type in GUARDED and action != "view":
        return False
    if paction == "*":
        return True
    return action in IMPLIES.get(paction, {paction})


class Principal:
    """The caller of the current request, with its bindings loaded once."""

    def __init__(self, user: User | None, via: str = "", read_only: bool = False, label: str = ""):
        self.user = user
        self.via = via  # session, token, sso, system
        self.read_only = read_only
        self.label = label or (user.username if user else "anonymous")
        self._bindings = None
        self._grants = None
        self.token_id: int | None = None  # the API token it came with
        self.session_pwv: str = ""  # the session's password version

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.is_admin and self.user.active)

    def _load(self):
        if self._bindings is not None:
            return
        if self.user is None:
            self._bindings, self._grants = [], []
            return
        group_ids = [gr.id for gr in self.user.groups]
        cond = RoleBinding.user_id == self.user.id
        gcond = ObjectGrant.user_id == self.user.id
        if group_ids:
            cond = cond | RoleBinding.group_id.in_(group_ids)
            gcond = gcond | ObjectGrant.group_id.in_(group_ids)
        self._bindings = [(b.role.permissions, b.scopes or ["*"]) for b in db.session.scalars(db.select(RoleBinding).where(cond))]
        self._grants = list(db.session.scalars(db.select(ObjectGrant).where(gcond)))

    def can(self, object_type: str, action: str, facts: Facts | None = None) -> bool:
        if self.user is None or not self.user.active:
            return False
        if self.read_only and action != "view":
            return False
        if self.is_admin:
            return True
        self._load()
        facts = facts or Facts()
        for perms, scopes in self._bindings:
            if any(perm_matches(p, object_type, action) for p in perms) and any(scope_matches(s, facts, action) for s in scopes):
                return True
        for gr in self._grants:
            if _grant_type_ok(gr.object_type, object_type, action) and any(
                a == "*" or action in IMPLIES.get(a, {a}) for a in gr.actions
            ) and any(fnmatchcase(n, gr.object_name) for n in (facts.names or {facts.name})):
                return True
        return False

    def can_any(self, object_type: str, action: str) -> bool:
        """Whether the caller has this permission anywhere (for showing buttons)."""
        if self.user is None or not self.user.active or (self.read_only and action != "view"):
            return False
        if self.is_admin:
            return True
        self._load()
        if any(any(perm_matches(p, object_type, action) for p in perms) for perms, _ in self._bindings):
            return True
        return any(_grant_type_ok(gr.object_type, object_type, action) and any(a == "*" or action in IMPLIES.get(a, {a}) for a in gr.actions)
                   for gr in self._grants)

    def summary(self) -> dict:
        return {t: [a for a in ACTIONS if self.can_any(t, a)] for t in OBJECT_TYPES}


def current() -> Principal:
    return getattr(g, "principal", None) or Principal(None)


def require(object_type: str, action: str, facts: Facts | None = None) -> None:
    p = current()
    if p.user is None:
        abort(401)
    if not p.can(object_type, action, facts):
        abort(403, description=f"you lack {object_type}:{action} for '{facts.name if facts else ''}'".replace(" for ''", ""))


def require_admin() -> None:
    p = current()
    if p.user is None:
        abort(401)
    if not p.is_admin or p.read_only:
        abort(403, description="administrators only")


def ensure_builtin_roles() -> None:
    for name, desc, perms in BUILTIN_ROLES:
        role = db.session.scalar(db.select(Role).where(Role.name == name))
        if role is None:
            db.session.add(Role(name=name, description=desc, permissions=perms, builtin=True))
        elif role.builtin and (role.permissions != perms or role.description != desc):
            role.permissions, role.description = perms, desc  # built-ins follow the release
    db.session.flush()


def validate_permissions(perms: list[str]) -> list[str]:
    bad = []
    for p in perms:
        t, _, a = str(p).partition(":")
        if t not in ("*", *OBJECT_TYPES) or a not in ("*", *ACTIONS):
            bad.append(p)
    return bad


def validate_scopes(scopes: list[str]) -> list[str]:
    return [s for s in scopes if not SCOPE_RE.match(str(s))]
