"""Patch state: updates waiting, reboots needed, last patched, per host (patches.py)."""
from __future__ import annotations

from flask import g, jsonify, request

from .. import patches, rbac, sites, store
from . import body, bp, err


@bp.get("/patches")
def list_patches():
    """Every visible host with its merged patch state; ?site= for one site, ?state=security|reboot|… to filter."""
    p = g.principal
    want = request.args.get("state", "")
    site = request.args.get("site", "")
    with sites.scoped(site):
        states = patches.overview()
    rows, visible = [], {}
    for e in store.host_index():
        h = e["host"]
        if h["name"] not in states or not p.can("host", "view", e["facts"]):
            continue
        s = visible[h["name"]] = states[h["name"]]
        if want and s["state"] != want and not (want == "reboot" and s["reboot"]):
            continue
        rows.append({"name": h["name"], "site": h["site"], "fun": h.get("fun") or "", "zone": h["zone"], "status": h["status"],
                     "kind": h.get("kind") or "device", "runs_on": h.get("runs_on") or "", **{k: v for k, v in s.items() if k != "sources"},
                     "sources": [x["source"] for x in s["sources"]]})
    return jsonify({"hosts": rows, "counts": patches.counts(visible), "stale_days": patches.STALE.days, "sites": [s.code for s in sites.all_sites()]})


@bp.get("/hosts/<name>/patches")
def host_patches(name):
    h = store.find_host(name)
    if h is None or not g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
        return err(404, f"no host '{name}'")
    return jsonify(patches.summary(h.name))


@bp.post("/patches")
def report_patches():
    """A host (or a script for several) reports its updates:
    {"host": "srv-vm-dns-01", "pending": 3, "security": 1, "reboot": false, "packages": [...], "os": "Ubuntu 24.04"}
    or {"source": "ansible", "hosts": {"srv-vm-dns-01": {...}, ...}}. Needs host:edit on each host."""
    data = body()
    source = str(data.get("source") or "agent")
    if isinstance(data.get("hosts"), dict):
        results = data["hosts"]
    elif data.get("host"):
        results = {str(data["host"]): data}
    else:
        return err(422, "send host and its state, or hosts: {name: state}")
    names = {}
    for name in results:
        h = store.find_host(str(name))
        if h is None:
            continue
        rbac.require("host", "edit", rbac.host_facts(store.host_access(h)))
        names[h.name] = results[name]
    if not names:
        return err(404, "none of these hosts is in the inventory")
    out = patches.report(source, names)
    return jsonify(out | {"skipped": sorted(set(map(str, results)) - set(out["saved"]))})


@bp.get("/patches/agent")
def patch_agent():
    """The labdex-patch-report script, to install on hosts (Python 3, standard library only)."""
    from flask import send_file

    from ..agent import patch_report
    return send_file(patch_report.__file__, mimetype="text/x-python", as_attachment=True, download_name="labdex-patch-report")
