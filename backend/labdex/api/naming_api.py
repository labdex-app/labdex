"""Hostname building blocks and the next free name."""
from __future__ import annotations

from flask import jsonify, request

from .. import audit, hostnames, rbac
from ..models import Host, NamingPart, db
from . import body, bp, err


@bp.get("/naming")
def get_naming():
    """The lists, plus roles that hostnames use without being defined (the role part is free text)."""
    p = hostnames.parts()
    defined = {r["code"] for r in p["role"]}
    p["roles_in_use"] = sorted({s[2] for (n,) in db.session.execute(db.select(Host.name))
                                if (s := hostnames._split(n)) and s[2] not in defined})
    return jsonify(p)


@bp.get("/naming/next")
def next_hostname():
    a = request.args
    parts = [str(a.get(k, "")).strip().lower() for k in ("category", "type", "role")]
    if not all(parts):
        return err(422, "give category, type and role")
    if not all(hostnames.CODE_RE.match(x) for x in parts):
        return err(422, "each part: 1–16 lowercase letters or digits")
    return jsonify(hostnames.next_name(*parts, exclude=a.get("exclude")))


@bp.post("/naming/<kind>")
@bp.put("/naming/<kind>/<code>")
def save_part(kind, code=None):
    rbac.require_admin()
    existing = None
    if code:
        existing = db.session.scalar(db.select(NamingPart).where(NamingPart.kind == kind, NamingPart.code == code))
        if existing is None:
            return err(404, f"no {kind} '{code}'")
    before = hostnames.part_dict(existing) if existing else None
    p = hostnames.save(kind, body(), existing)
    db.session.flush()
    audit.record("update" if existing else "create", "naming", f"{kind}:{p.code}", before, hostnames.part_dict(p))
    db.session.commit()
    return jsonify(hostnames.parts()), 200 if existing else 201


@bp.delete("/naming/<kind>/<code>")
def delete_part(kind, code):
    rbac.require_admin()
    hostnames.delete(kind, code)
    audit.record("delete", "naming", f"{kind}:{code}", {"code": code}, None)
    db.session.commit()
    return jsonify(hostnames.parts())


# ------------------------------------------------------------ fun name pools
@bp.get("/naming/pools")
def list_pools():
    from .. import funpools
    from ..models import FunPool
    used = funpools.used_names()
    return jsonify([funpools.to_dict(p, used) for p in db.session.scalars(db.select(FunPool).order_by(FunPool.name))])


@bp.post("/naming/pools")
@bp.put("/naming/pools/<name>")
def save_pool(name=None):
    from .. import funpools
    from ..models import FunPool
    rbac.require_admin()
    existing = None
    if name:
        existing = db.session.scalar(db.select(FunPool).where(FunPool.name == name))
        if existing is None:
            return err(404, f"no pool '{name}'")
    before = funpools.to_dict(existing) if existing else None
    p = funpools.save(body(), existing)
    db.session.flush()
    audit.record("update" if existing else "create", "naming", f"pool:{p.name}", before, funpools.to_dict(p))
    db.session.commit()
    return jsonify(funpools.to_dict(p)), 200 if existing else 201


@bp.delete("/naming/pools/<name>")
def delete_pool(name):
    from .. import funpools
    from ..models import FunPool
    rbac.require_admin()
    p = db.session.scalar(db.select(FunPool).where(FunPool.name == name))
    if p is None:
        return err(404, f"no pool '{name}'")
    audit.record("delete", "naming", f"pool:{p.name}", funpools.to_dict(p), None)
    db.session.delete(p)
    db.session.commit()
    return "", 204


@bp.get("/naming/fun-suggest")
def fun_suggest():
    """?category=srv&zone=DC_TC[&pool=stars][&count=8]: unused fun names to pick from."""
    from .. import funpools
    a = request.args
    return jsonify(funpools.suggest(a.get("category", "").lower(), a.get("zone", "").upper(), a.get("pool", ""),
                                    a.get("count", 8, type=int)))
