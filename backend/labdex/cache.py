"""Small per-revision cache for things derived from the whole inventory.

Lint, DNS records, generated aliases and the zone tables are recomputed only
when the inventory changes. The key is (app, inventory revision, local
generation): the revision is the newest inventory audit event, read once per
request, so changes made by another process (the CLI) are picked up; the local
generation goes up on every flush that touches inventory tables, so writes in
this process are seen at once, even before they're audited.
"""
from __future__ import annotations

import threading

from flask import current_app, g, has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session

_store: dict = {}
_lock = threading.Lock()
_generation = [0]
WATCHED = {"Host", "Interface", "Service", "Network", "AliasGroup", "Area", "Zone", "Setting", "NamingPart",
           "Feature", "HostModule", "AuditEvent", "Secret", "ZonePolicy", "Tag"}


def bump() -> None:
    with _lock:
        _generation[0] += 1
    if has_app_context():
        g.pop("_ldx_rev", None)


@event.listens_for(Session, "after_flush")
def _after_flush(session, _ctx):
    for obj in (*session.new, *session.dirty, *session.deleted):
        if type(obj).__name__ in WATCHED:
            bump()
            return


def _revision() -> int:
    from .audit import inventory_revision
    if not has_app_context():
        return inventory_revision()
    rev = g.get("_ldx_rev")
    if rev is None:
        rev = g._ldx_rev = inventory_revision()
    return rev


def memo(name: str, fn):
    app = current_app._get_current_object()
    key = (id(app), app.config.get("SQLALCHEMY_DATABASE_URI"), name)
    stamp = (_revision(), _generation[0])
    hit = _store.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    value = fn()
    _store[key] = (stamp, value)
    return value
