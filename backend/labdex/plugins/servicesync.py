"""Shared by plugins that find services elsewhere (Traefik, Docker): plan the services a host
should get and apply them to the inventory.

A wanted service is {"name", "port", "proto", "scheme", "dns_name", "description", "path"}. It
matches a service the host already has with the same DNS name, else the same port and
protocol, else the same name. A match that differs is an update of the fields the source
knows (an existing link, description or path you wrote is kept); no match is a create.
Nothing is ever removed.
"""
from __future__ import annotations

import re

from .base import Change

FIELDS = ("port", "proto", "scheme", "dns_name", "path")
HOST_RE = re.compile(r"Host\(([^)]*)\)")
PATH_RE = re.compile(r"PathPrefix\(`([^`]*)`\)")
TLS_ENTRYPOINTS = {"websecure", "https", "web-secure", "secure", "443"}


def rule_hosts(rule: str) -> list[str]:
    """Host names in a Traefik rule: Host(`a`) || Host(`b`, `c`)."""
    out = []
    for args in HOST_RE.findall(rule or ""):
        out += [a.strip().strip("`\"'").lower() for a in args.split(",") if a.strip().strip("`\"'")]
    return out


def rule_path(rule: str) -> str:
    p = (PATH_RE.findall(rule or "") or [""])[0]
    return "" if p == "/" else p


def clean_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (s or "").lower().split("@")[0]).strip("-")
    return (s or "service")[:63]


def _match(existing: list[dict], want: dict) -> dict | None:
    if want.get("dns_name"):
        hit = next((s for s in existing if (s.get("dns_name") or "") == want["dns_name"]), None)
        if hit:
            return hit
    if want.get("port"):
        hit = next((s for s in existing if s.get("port") == want["port"] and (s.get("proto") or "tcp") == want.get("proto", "tcp")
                    and not (s.get("dns_name") and want.get("dns_name") and s["dns_name"] != want["dns_name"])), None)
        if hit:
            return hit
    return next((s for s in existing if s["name"] == want["name"]), None)


def plan(ctx, wanted: dict[str, list[dict]], source: str) -> list[Change]:
    """wanted: {host name: [service, …]} → changes ("service" kind; data: host, service)."""
    changes = []
    hosts = {x["name"]: x for x in ctx.inventory.hosts(include_offline=True)}
    for hname, services in sorted(wanted.items()):
        h = hosts.get(hname)
        if h is None:
            continue
        existing = list(h["services"])
        taken = {s["name"] for s in existing}
        for w in services:
            w = {"proto": "tcp", "scheme": "", "dns_name": "", "path": "", "description": "", **w}
            cur = _match(existing, w)
            if cur is None:
                name = w["name"]
                n = 2
                while name in taken:
                    name = f"{w['name']}-{n}"[:63]
                    n += 1
                taken.add(name)
                w["name"] = name
                link = f" · {w['scheme']}://{w['dns_name'].rstrip('.')}" if w["dns_name"] and w["scheme"] else ""
                changes.append(Change("create", "service", f"{hname} {name}", f"{w['port'] or '—'}/{w['proto']}{link} ({source})",
                                      None, w, {"host": hname, "service": w}))
                existing.append(w)
                continue
            diff = {k: w[k] for k in FIELDS if w.get(k) and cur.get(k) != w[k]}
            if diff:
                changes.append(Change("update", "service", f"{hname} {cur['name']}", ", ".join(f"{k} → {v}" for k, v in diff.items()),
                                      {k: cur.get(k) for k in diff}, diff, {"host": hname, "service": {**diff, "name": cur["name"]}}))
    return changes


def apply(ctx, changes: list[Change], source: str) -> dict:
    """Write the planned services into the hosts (re-read: edits made meanwhile are kept)."""
    from .. import audit, store
    from ..models import db
    by_host: dict[str, list[Change]] = {}
    for c in changes:
        if c.kind == "service":
            by_host.setdefault(c.data["host"], []).append(c)
    done = 0
    for hname, cs in by_host.items():
        h = store.find_host(hname)
        if h is None:
            ctx.log(f"{hname}: gone, skipped")
            continue
        before = store.host_to_dict(h)
        services = [dict(s) for s in before["services"]]
        for c in cs:
            svc = c.data["service"]
            if c.op == "create":
                if any(s["name"] == svc["name"] for s in services):
                    ctx.log(f"{hname} {svc['name']}: exists now, skipped")
                    continue
                services.append({k: v for k, v in svc.items() if k != "addresses"})
            else:
                cur = next((s for s in services if s["name"] == svc["name"]), None)
                if cur is None:
                    ctx.log(f"{hname} {svc['name']}: gone, skipped")
                    continue
                cur.update({k: v for k, v in svc.items() if k != "name"})
            done += 1
        for s in services:
            s.pop("addresses", None)
        try:
            h = store.save_host({**before, "services": services}, h)
        except store.ValidationError as exc:
            db.session.rollback()
            ctx.log(f"{hname}: not changed: " + "; ".join(f"{e['field']}: {e['message']}" for e in exc.errors))
            continue
        db.session.flush()
        audit.record("update", "host", h.name, before, store.host_to_dict(h), summary=f"services from {source}",
                     actor=(f"{source}", "system"))
        db.session.commit()
        for c in cs:
            ctx.log(f"{c.op} {c.name}: ok")
    if done:
        from . import jobs
        jobs.emit("host.updated")
    return {"applied": done}
