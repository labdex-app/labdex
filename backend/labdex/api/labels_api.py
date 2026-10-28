"""Printable labels (SVG) for hosts and cables."""
from __future__ import annotations

from flask import Response, g, request

from .. import labels, naming, rbac, store
from ..models import Link, db
from . import bp, err
from .topology_api import _visible_hosts


def _base() -> str:
    return (naming.get_setting("public_url") or request.host_url).rstrip("/")


def _svg(text: str, name: str) -> Response:
    resp = Response(text, mimetype="image/svg+xml")
    resp.headers["Content-Disposition"] = f'inline; filename="{name}.svg"'
    return resp


@bp.get("/hosts/<name>/label.svg")
def host_label(name):
    """?size=62x29 (or 50x25, 89x36)"""
    h = store.find_host(name)
    if h is None or not g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
        return err(404, f"no host '{name}'")
    url = f"{_base()}/hosts/{h.uid}"
    return _svg(labels.host_label(store.host_to_dict(h), url, labels.parse_size(request.args.get("size"))), h.name)


@bp.get("/cables/label.svg")
def cable_label():
    """?host=&port= : the cable on that port, with both ends. Ends you may not see read "a host you can't see"."""
    h = store.find_host(str(request.args.get("host", "")))
    port = str(request.args.get("port", "")).strip()
    visible = set(_visible_hosts())
    if h is None or h.name not in visible or not port:
        return err(404, "no such cable")
    lk = db.session.scalar(db.select(Link).where(((Link.a_host_id == h.id) & (Link.a_port == port)) | ((Link.b_host_id == h.id) & (Link.b_port == port))))
    if lk is None:
        return err(404, "no cable on that port")
    ends = [(lk.a_host.name, lk.a_port), (lk.b_host.name, lk.b_port)]
    ends = [(n if n in visible else "a host you can't see", p if n in visible else "") for n, p in ends]
    url = f"{_base()}/cables?host={h.name}&port={port}"
    cable = {"label": lk.label, "medium": lk.medium, "speed": lk.speed, "length": lk.length}
    return _svg(labels.cable_label(cable, ends[0], ends[1], url, labels.parse_size(request.args.get("size"))), lk.label or "cable")
