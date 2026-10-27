"""Which integrations apply to which hosts.

An integration applies to the hosts its selectors match (same syntax as RBAC
scopes: *, zone:DC_*, area:CA, network:srv, tag:admin, name:srv-*). Any host
can override that (on/off) and carry its own host settings, as declared by the
plugin's `host_settings`.
"""
from __future__ import annotations

from flask import current_app

from . import rbac, store
from .models import Host, HostModule, Integration, db


def matches(integ: Integration, host: dict) -> bool:
    facts = rbac.host_facts(host)
    return any(rbac.scope_matches(s, facts) for s in (integ.selectors or []))


def override(integ: Integration, host_name: str) -> HostModule | None:
    return db.session.scalar(db.select(HostModule).where(
        HostModule.integration_id == integ.id, HostModule.host_name == host_name))


def state(integ: Integration, host: dict, hm: HostModule | None = None, *, lookup=True) -> tuple[bool, str]:
    """(enabled, why) for one host."""
    if lookup and hm is None:
        hm = override(integ, host["name"])
    if hm is not None and hm.enabled is not None:
        return hm.enabled, "on for this host" if hm.enabled else "off for this host"
    return (True, "selector") if matches(integ, host) else (False, "not selected")


def enabled_hosts(integ: Integration, include_offline=False) -> list[dict]:
    """Host dicts this integration applies to, each with `module_settings` (raw, encrypted secrets)."""
    hms = {hm.host_name: hm for hm in db.session.scalars(db.select(HostModule).where(HostModule.integration_id == integ.id))}
    q = db.select(Host).order_by(Host.name)
    if not include_offline:
        q = q.where(Host.status != "offline")
    out = []
    for h in db.session.scalars(q):
        d = store.host_to_dict(h)
        on, _ = state(integ, d, hms.get(h.name), lookup=False)
        if on:
            d["module_settings"] = (hms[h.name].settings if h.name in hms else {}) or {}
            out.append(d)
    return out


def rename_host(old: str, new: str) -> None:
    if old != new:
        for hm in db.session.scalars(db.select(HostModule).where(HostModule.host_name == old)):
            hm.host_name = new
        from . import health
        from .models import JobHost, JobTemplate
        health.rename(old, new)
        for t in db.session.scalars(db.select(JobTemplate).where(JobTemplate.host == old)):
            t.host = new
        # run history follows the host (a job may already list the new name)
        taken = set(db.session.scalars(db.select(JobHost.job_id).where(JobHost.host == new)))
        for jh in db.session.scalars(db.select(JobHost).where(JobHost.host == old)):
            if jh.job_id in taken:
                db.session.delete(jh)
            else:
                jh.host = new


def delete_host(name: str) -> None:
    from . import health
    db.session.execute(db.delete(HostModule).where(HostModule.host_name == name))
    health.forget(name)


def vault():
    return current_app.extensions["labdex"]["vault"]
