"""The DNS overview: every record labdex generates, and what DNS servers (plugins that
provide `dns`) actually hold, side by side."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from flask import current_app, g, jsonify

from .. import naming, rbac
from ..generators import dns
from ..models import Integration, db
from . import bp

TIMEOUT = 20


def _server(app, integ_id: int) -> dict:
    from ..plugins import resolved_settings
    from ..plugins.base import Context
    with app.app_context():
        try:
            integ = db.session.get(Integration, integ_id)
            spec = app.extensions["labdex"]["registry"].get(integ.plugin_id)
            settings = resolved_settings(spec, integ.settings or {}, app.extensions["labdex"]["vault"])
            log = []
            plugin = spec.plugin_class()(Context(integ.name, spec, settings, log.append, integration=integ))
            out = plugin.server_records()
            return {"integration": integ.name, "plugin": spec.name, **out, "log": log[-5:]}
        except Exception as exc:  # one broken server never breaks the page
            return {"integration": integ.name if integ else "?", "error": f"{type(exc).__name__}: {exc}"[:300], "records": []}
        finally:
            db.session.remove()


@bp.get("/dns")
def dns_overview():
    p = g.principal
    hosts_ok = {}

    def visible(host: str) -> bool:
        if not host:
            return True
        if host not in hosts_ok:
            from .. import store
            h = store.find_host(host)
            hosts_ok[host] = h is not None and p.can("host", "view", rbac.host_facts(store.host_to_dict(h)))
        return hosts_ok[host]

    domain = naming.get_setting("domain")
    generated = [{"name": r.fqdn(domain).lower().rstrip("."), "type": r.type, "value": r.value.rstrip(".") if r.type == "CNAME" else r.value,
                  "host": r.host or "", "zone": r.zone or domain, "public": r.public}
                 for r in dns.records() if visible(r.host or "")]
    reg = current_app.extensions["labdex"]["registry"]
    ids = []
    for i in db.session.scalars(db.select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)):
        spec = reg.get(i.plugin_id)
        if spec and not spec.error and "dns" in spec.provides and spec.plugin_class() is not None \
                and hasattr(spec.plugin_class(), "server_records") and p.can("integration", "view", rbac.named_facts(i.name)):
            ids.append(i.id)
    app = current_app._get_current_object()
    servers = []
    if ids:
        with ThreadPoolExecutor(max_workers=min(4, len(ids))) as pool:
            futs = [pool.submit(_server, app, i) for i in ids]
            for f, iid in zip(futs, ids):
                try:
                    servers.append(f.result(timeout=TIMEOUT))
                except FutureTimeout:
                    servers.append({"integration": db.session.get(Integration, iid).name, "error": f"no answer within {TIMEOUT} s", "records": []})
    for s in servers:
        s["records"] = [r for r in s.get("records", []) if visible(r.get("host", ""))]
    return jsonify({"domain": domain, "zones": dns.zones(), "generated": generated, "servers": servers})
