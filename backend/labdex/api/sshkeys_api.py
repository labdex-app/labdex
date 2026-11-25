"""SSH login keys through the agents (sshkeys.py). Keys, grants, break-glass keys and applies are
for administrators; a host's key status is for whoever may see the host."""
from __future__ import annotations

from flask import g, jsonify

from .. import agents, rbac, sshkeys, store
from ..models import SSHKey, SSHKeyGrant, db
from . import body, bp, err


def _host(name: str, action: str):
    h = store.find_host(name)
    if h is None or not g.principal.can("host", "view", rbac.host_facts(store.host_access(h))):
        return None
    rbac.require("host", action, rbac.host_facts(store.host_access(h)))
    return h


@bp.get("/ssh-keys")
def ssh_keys():
    rbac.require_admin()
    return jsonify({"keys": sshkeys.list_keys(), "hosts": sshkeys.overview(), "types": list(sshkeys.TYPES),
                    "min_rsa_bits": sshkeys.MIN_RSA_BITS})


@bp.post("/ssh-keys")
def add_ssh_key():
    rbac.require_admin()
    try:
        k = sshkeys.add_key(body(), g.principal.label)
    except sshkeys.SSHKeyError as e:
        db.session.rollback()
        return err(400, str(e))
    db.session.commit()
    return jsonify(sshkeys.key_to_dict(k, [])), 201


def _key_or_404(key_id: int):
    rbac.require_admin()
    return db.session.get(SSHKey, key_id)


@bp.patch("/ssh-keys/<int:key_id>")
def update_ssh_key(key_id):
    k = _key_or_404(key_id)
    if k is None:
        return err(404, "no such key")
    try:
        sshkeys.update_key(k, body())
    except sshkeys.SSHKeyError as e:
        db.session.rollback()
        return err(400, str(e))
    db.session.commit()
    return jsonify(sshkeys.key_to_dict(k))


@bp.delete("/ssh-keys/<int:key_id>")
def delete_ssh_key(key_id):
    k = _key_or_404(key_id)
    if k is None:
        return err(404, "no such key")
    sshkeys.delete_key(k)
    db.session.commit()
    return "", 204


@bp.post("/ssh-keys/<int:key_id>/grants")
def grant_ssh_key(key_id):
    k = _key_or_404(key_id)
    if k is None:
        return err(404, "no such key")
    data = body()
    try:
        gr = sshkeys.grant(k, data.get("host", ""), data.get("user", ""), g.principal.label)
    except sshkeys.SSHKeyError as e:
        db.session.rollback()
        return err(400, str(e))
    db.session.commit()
    return jsonify({"id": gr.id, "host": gr.host, "user": gr.user}), 201


@bp.delete("/ssh-keys/grants/<int:grant_id>")
def ungrant_ssh_key(grant_id):
    rbac.require_admin()
    gr = db.session.get(SSHKeyGrant, grant_id)
    if gr is None:
        return err(404, "no such grant")
    sshkeys.revoke_grant(gr)
    db.session.commit()
    return "", 204


@bp.get("/hosts/<name>/ssh-keys")
def host_ssh_keys(name):
    h = _host(name, "view")
    if h is None:
        return err(404, f"no host '{name}'")
    return jsonify(sshkeys.status(h.name))


@bp.post("/hosts/<name>/ssh-keys/report")
def report_host_ssh_keys(name):
    h = _host(name, "edit")
    a = agents.current(h.name) if h else None
    if a is None:
        return err(404, f"no agent on '{name}'")
    try:
        j = agents.queue(a, "ssh_keys.report", by=g.principal.label)
    except agents.AgentError as e:
        db.session.rollback()
        return err(409, str(e))
    db.session.commit()
    return jsonify(agents.job_to_dict(j)), 201


@bp.post("/hosts/<name>/ssh-keys/apply")
def apply_host_ssh_keys(name):
    """Put labdex's keys for the host's ssh_users (and its break-glass key) on the host."""
    h = _host(name, "view")
    if h is None:
        return err(404, f"no host '{name}'")
    rbac.require_admin()
    try:
        j = sshkeys.apply(h.name, g.principal.label)
    except (sshkeys.SSHKeyError, agents.AgentError) as e:
        db.session.rollback()
        return err(409, str(e))
    db.session.commit()
    return jsonify(agents.job_to_dict(j)), 201


@bp.put("/hosts/<name>/ssh-keys/break-glass")
def set_host_break_glass(name):
    """{user, public_key}, or {user, generate: true}: then the answer has the private key, this once."""
    h = _host(name, "view")
    if h is None:
        return err(404, f"no host '{name}'")
    rbac.require_admin()
    data = body()
    private = None
    try:
        if data.get("generate") is True:
            bg, private = sshkeys.generate_break_glass(h.name, data.get("user", ""), g.principal.label)
        else:
            bg = sshkeys.set_break_glass(h.name, data.get("user", ""), str(data.get("public_key") or ""), g.principal.label)
    except sshkeys.SSHKeyError as e:
        db.session.rollback()
        return err(400, str(e))
    db.session.commit()
    out = sshkeys.break_glass_to_dict(bg)
    if private:
        out["private_key"] = private
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "no-store"
    return resp
