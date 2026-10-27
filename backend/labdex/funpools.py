"""Fun name pools: lists of memorable names to suggest for hosts.

Four neutral pools ship with labdex (added once; delete or disable them freely).
Add your own by pasting a list. A pool can apply to categories, areas or zones
(category:srv, zone:DC_*, area:CA); matching pools are suggested first.
"""
from __future__ import annotations

import fnmatch
import random
import re

from . import naming, store
from .models import FunPool, Host, db

SELECTOR_RE = re.compile(r"^(\*|(category|zone|area):[A-Za-z0-9_*?]+)$")
SEEDED = "fun_pools_seeded"

BUILTIN = {
    "greek-gods": ("Greek gods, titans and heroes", """
        zeus hera poseidon demeter athena apollo artemis ares aphrodite hephaestus hermes hestia dionysus hades
        persephone hecate eos helios selene nyx gaia uranus kronos rhea oceanus tethys hyperion theia themis
        mnemosyne atlas prometheus epimetheus iris nike nemesis hypnos thanatos eris pan asclepius hebe""") ,
    "stars": ("Bright stars", """
        sirius canopus arcturus vega capella rigel procyon achernar betelgeuse hadar altair acrux aldebaran
        antares spica pollux fomalhaut deneb mimosa regulus adhara castor shaula gacrux bellatrix elnath
        miaplacidus alnilam alnair alnitak alioth dubhe mirfak wezen sargas kaus avior alkaid menkalinan
        atria alhena peacock alsephina mirzam alphard polaris hamal diphda nunki menkent mirach saiph""") ,
    "trees": ("Trees", """
        oak birch maple ash elm beech alder aspen willow poplar linden hornbeam chestnut walnut hazel rowan
        yew pine spruce fir larch cedar cypress juniper sequoia redwood hemlock sycamore magnolia acacia
        baobab eucalyptus olive cherry plum apple pear holly hawthorn elder laurel myrtle""") ,
    "minerals": ("Minerals and gemstones", """
        quartz feldspar mica calcite dolomite gypsum halite fluorite apatite topaz corundum diamond beryl
        emerald aquamarine garnet olivine peridot jade jasper agate onyx opal obsidian pyrite galena
        hematite magnetite malachite azurite turquoise lapis zircon tourmaline spinel amethyst citrine
        rutile cinnabar sulfur graphite talc""") ,
}


def _names(text) -> list[str]:
    raw = re.split(r"[\s,;]+", text) if isinstance(text, str) else list(text or [])
    return list(dict.fromkeys(str(n).strip().lower() for n in raw if str(n).strip()))


def ensure_builtin() -> None:
    """Add the built-in pools once (a deleted one stays deleted)."""
    if naming.get_setting(SEEDED) == "true":
        return
    have = {p.name for p in db.session.scalars(db.select(FunPool))}
    for name, (desc, names) in BUILTIN.items():
        if name not in have:
            db.session.add(FunPool(name=name, description=desc, names=_names(names), builtin=True))
    store.save_settings({SEEDED: "true"})
    db.session.flush()


def to_dict(p: FunPool, used: set | None = None) -> dict:
    used = used if used is not None else used_names()
    return {"name": p.name, "description": p.description, "names": list(p.names or []), "applies_to": list(p.applies_to or []),
            "builtin": p.builtin, "enabled": p.enabled, "free": sum(1 for n in p.names or [] if n not in used),
            "used": [n for n in p.names or [] if n in used]}


def used_names() -> set[str]:
    out = set()
    for h in db.session.scalars(db.select(Host)):
        out.add(h.name)
        out.update(h.fun_names)
    return out


def save(data: dict, existing: FunPool | None = None) -> FunPool:
    errors = []
    name = str(data.get("name", "")).strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", name):
        errors.append({"field": "name", "message": "lowercase letters, digits and dashes"})
    clash = db.session.scalar(db.select(FunPool).where(FunPool.name == name))
    if clash is not None and clash is not existing:
        errors.append({"field": "name", "message": f"pool '{name}' exists"})
    names = _names(data.get("names") or [])
    bad = [n for n in names if not naming.FUN_RE.match(n)]
    if bad:
        errors.append({"field": "names", "message": f"not usable as fun names: {', '.join(bad[:8])}{'…' if len(bad) > 8 else ''}"})
    if not names:
        errors.append({"field": "names", "message": "paste at least one name (spaces, commas or new lines between them)"})
    applies = data.get("applies_to") or []
    if isinstance(applies, str):
        applies = [a for a in re.split(r"[\s,]+", applies) if a]
    applies = list(dict.fromkeys(str(a).strip() for a in applies if str(a).strip()))
    badsel = [a for a in applies if not SELECTOR_RE.match(a)]
    if badsel:
        errors.append({"field": "applies_to", "message": f"use category:srv, zone:DC_*, area:CA or *: {', '.join(badsel)}"})
    if errors:
        raise store.ValidationError(errors)
    p = existing or FunPool(builtin=False)
    p.name, p.description, p.names, p.applies_to = name, str(data.get("description", ""))[:500], names, applies
    p.enabled = bool(data.get("enabled", True))
    if existing is None:
        db.session.add(p)
    return p


def _matches(p: FunPool, category: str, zone: str) -> bool:
    area = zone.split("_")[0] if zone else ""
    facts = {"category": category, "zone": zone, "area": area}
    for sel in p.applies_to or []:
        if sel == "*":
            return True
        kind, _, pat = sel.partition(":")
        if facts.get(kind) and fnmatch.fnmatchcase(facts[kind], pat):
            return True
    return False


def suggest(category: str = "", zone: str = "", pool: str = "", count: int = 8, seed: int | None = None) -> dict:
    """Unused names: from the named pool, else from pools that apply to this category/zone, else from all enabled pools."""
    used = used_names()
    pools = [p for p in db.session.scalars(db.select(FunPool).order_by(FunPool.name)) if p.enabled or p.name == pool]
    if pool:
        chosen = [p for p in pools if p.name == pool]
    else:
        chosen = [p for p in pools if _matches(p, category, zone)] or pools
    rnd = random.Random(seed)
    out = []
    for p in chosen:
        free = [n for n in p.names or [] if n not in used]
        rnd.shuffle(free)
        out += [{"name": n, "pool": p.name} for n in free]
    if len(chosen) > 1:
        rnd.shuffle(out)
    return {"names": out[:max(1, min(count, 50))], "pools": [p.name for p in chosen],
            "matched": bool(pool) or any(_matches(p, category, zone) for p in pools)}


def export_dict(p: FunPool) -> dict:
    """For the inventory YAML: a built-in pool whose names are unchanged is written without them."""
    d = {"name": p.name}
    unchanged = p.builtin and p.name in BUILTIN and list(p.names or []) == _names(BUILTIN[p.name][1])
    if p.builtin:
        d["builtin"] = True
    if p.description and not (unchanged and p.description == BUILTIN[p.name][0]):
        d["description"] = p.description
    if not unchanged:
        d["names"] = list(p.names or [])
    if p.applies_to:
        d["applies_to"] = list(p.applies_to)
    if not p.enabled:
        d["enabled"] = False
    return d


def import_one(d: dict) -> FunPool:
    d = dict(d)
    if d.get("builtin") and d.get("name") in BUILTIN:
        d.setdefault("names", _names(BUILTIN[d["name"]][1]))
        d.setdefault("description", BUILTIN[d["name"]][0])
    p = save(d)
    p.builtin = bool(d.get("builtin"))
    return p
