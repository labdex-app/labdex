"""What breaks if these hosts go down (labdex.impact)."""
from __future__ import annotations

from flask import g, jsonify, request

from .. import impact, rbac, store
from ..models import Host, db
from . import bp, err


@bp.get("/impact")
def get_impact():
    """?host=a&host=b: the impact of those hosts going down. Lists only hosts you may see."""
    p = g.principal
    names = [n.strip().lower() for n in request.args.getlist("host") if n.strip()][:50]
    if not names:
        return err(422, "name at least one host: ?host=<name>")
    seen: dict[int, bool] = {}

    def visible(h: Host) -> bool:
        if h.id not in seen:
            seen[h.id] = p.can("host", "view", rbac.host_facts(store.host_access(h)))
        return seen[h.id]
    for n in names:
        h = db.session.scalar(db.select(Host).where(Host.name == n))
        if h is not None and not visible(h):
            return err(404, f"no host '{n}'")
    return jsonify(impact.analyse(names, visible))
