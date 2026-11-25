"""SSH login keys through the agent (sshkeys.py): key checks, grants, the plan against the agent's
report, apply and break-glass keys, and who may do what."""
import base64
import struct

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from labdex import agents, sshkeys
from labdex.models import Agent, AgentJob, AuditEvent, db

from . import test_agents

DNS = test_agents.DNS
hub = test_agents.hub


def ed_key(comment="alice@example.com"):
    pub = ed25519.Ed25519PrivateKey.generate().public_key()
    line = pub.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    return f"{line} {comment}" if comment else line


def rsa_key(bits):
    pub = rsa.generate_private_key(public_exponent=65537, key_size=bits).public_key()
    return pub.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def test_parse_takes_plain_keys_only():
    line = ed_key()
    p = sshkeys.parse(line)
    assert p["type"] == "ssh-ed25519" and p["comment"] == "alice@example.com" and p["line"] == line
    assert p["sha256"].startswith("SHA256:") and "=" not in p["sha256"]
    blob = line.split()[1]
    for bad in (f'command="/bin/sh" {line}', f"{line}\n{line}", f"ssh-dss {blob}", f"ecdsa-sha2-nistp256 {blob}",
                "ssh-ed25519 !!!", "ssh-ed25519", f"{line} $(reboot)", rsa_key(2048)):
        with pytest.raises(sshkeys.SSHKeyError):
            sshkeys.parse(bad)
    assert sshkeys.parse(rsa_key(3072))["bits"] == 3072


def test_fingerprint_is_what_ssh_keygen_prints():
    raw = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
    line = "ssh-ed25519 " + base64.b64encode(raw).decode()
    # ssh-keygen -lf of this key
    import hashlib
    want = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    assert sshkeys.parse(line)["sha256"] == want


def _agent(hub, users=("admin",), able=True):
    _, creds = test_agents.enrol(hub)
    a = db.session.get(Agent, creds["agent_id"])
    a.jobs = ["ping", "ssh_keys.apply", "ssh_keys.report"]
    a.ssh_users = list(users)
    if able:
        a.capabilities, a.host_allow = ["ssh_keys"], ["ssh_keys"]
    db.session.commit()
    return a


def _report(a, labdex=(), own=(), glass=None, user="admin", uses=True):
    def f(path, keys):
        return {"path": path, "keys": [{"type": "ssh-ed25519", "sha256": k, "comment": ""} for k in keys]}
    users = [{"user": user, "uid": 1000, "managed": True, "own": [f("/home/admin/.ssh/authorized_keys", own)] if own else [],
              "labdex": f(f"/etc/ssh/labdex/{user}.keys", labdex),
              "break_glass": f(f"/etc/ssh/labdex/{user}.break-glass", [glass]) if glass else None}]
    a.ssh_keys = {"users": users, "sshd": {"authorizedkeysfile": ".ssh/authorized_keys /etc/ssh/labdex/%u.keys"},
                  "uses": uses, "exclusive": False, "drop_in": uses, "at": 1}
    db.session.commit()


def test_keys_grants_and_the_plan(hub):
    admin = hub["admin"]
    a = _agent(hub)
    alice, bob = ed_key("alice"), ed_key("bob")
    r = admin.post("/api/ssh-keys", json={"name": "alice laptop", "owner": "alice", "public_key": alice})
    assert r.status_code == 201, r.get_json()
    kid = r.get_json()["id"]
    assert admin.post("/api/ssh-keys", json={"name": "again", "public_key": alice}).status_code == 400
    assert admin.post("/api/ssh-keys", json={"name": "bad", "public_key": f"from=\"x\" {bob}"}).status_code == 400
    bob_id = admin.post("/api/ssh-keys", json={"name": "bob", "public_key": bob}).get_json()["id"]
    assert admin.post(f"/api/ssh-keys/{kid}/grants", json={"host": DNS, "user": "admin"}).status_code == 201
    assert admin.post(f"/api/ssh-keys/{bob_id}/grants", json={"host": "*", "user": "admin"}).status_code == 201
    assert admin.post(f"/api/ssh-keys/{kid}/grants", json={"host": "no-such-host", "user": "admin"}).status_code == 400
    assert admin.post(f"/api/ssh-keys/{kid}/grants", json={"host": DNS, "user": "Root!"}).status_code == 400
    # a grant for a user the host doesn't hand to labdex shows as an issue, never goes on the host
    assert admin.post(f"/api/ssh-keys/{kid}/grants", json={"host": DNS, "user": "root"}).status_code == 201

    s = admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()
    assert s["state"] == "unknown" and "no report from the agent yet" in s["issues"]
    assert any("isn't in this host's ssh_users" in i for i in s["issues"])

    stranger = ed_key("stranger")
    _report(a, own=[sshkeys.parse(stranger)["sha256"]])
    s = admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()
    assert s["state"] == "drift" and s["break_glass_state"] == "missing"
    row = s["users"][0]
    assert {k["name"] for k in row["add"]} == {"alice laptop", "bob"} and row["remove"] == []
    assert s["unmanaged"][0]["sha256"] == sshkeys.parse(stranger)["sha256"] and s["unmanaged"][0]["known"] is None

    keys = admin.get("/api/ssh-keys").get_json()
    assert {k["name"] for k in keys["keys"]} == {"alice laptop", "bob"}
    assert keys["hosts"][0]["host"] == DNS and keys["hosts"][0]["add"] == 2

    r = admin.post(f"/api/hosts/{DNS}/ssh-keys/apply")
    assert r.status_code == 201, r.get_json()
    j = db.session.get(AgentJob, r.get_json()["id"])
    assert j.type == "ssh_keys.apply" and j.trigger == "ssh-keys"
    assert sorted(j.params["users"]["admin"]) == sorted([alice, bob]) and "root" not in j.params["users"]
    assert "break_glass" not in j.params

    # the agent's answer carries the report: the plan is empty now, but the break-glass key is missing
    fps = [sshkeys.parse(alice)["sha256"], sshkeys.parse(bob)["sha256"]]
    agents.finished(a, {"id": j.id, "ok": True, "result": {"ok": True, "changes": [], "report": {
        "users": [{"user": "admin", "uid": 1000, "own": [], "labdex": {"keys": [{"sha256": f} for f in fps]}}],
        "uses": True, "exclusive": True, "sshd": {}, "at": 2}}})
    db.session.commit()
    s = admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()
    assert s["state"] == "no_break_glass" and s["users"][0]["add"] == []

    # removing a key plans its removal
    assert admin.delete(f"/api/ssh-keys/{bob_id}").status_code == 204
    s = admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()
    assert s["users"][0]["remove"] == [fps[1]] and s["state"] == "drift"
    assert db.session.scalar(db.select(AuditEvent).where(AuditEvent.object_type == "ssh_key", AuditEvent.action == "delete"))


def test_break_glass_is_shown_once_and_never_removed_by_an_apply(hub):
    admin = hub["admin"]
    a = _agent(hub, users=("admin", "deploy"))
    assert admin.put(f"/api/hosts/{DNS}/ssh-keys/break-glass", json={"user": "nobody", "generate": True}).status_code == 400
    r = admin.put(f"/api/hosts/{DNS}/ssh-keys/break-glass", json={"user": "admin", "generate": True})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----") and r.headers["Cache-Control"] == "no-store"
    assert out["source"] == "generated" and out["public_key"].startswith("ssh-ed25519 ")
    # only the public half is kept
    s = admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()
    assert "private_key" not in s["break_glass"] and s["break_glass"]["sha256"] == out["sha256"]
    ev = db.session.scalar(db.select(AuditEvent).where(AuditEvent.action == "break-glass"))
    assert "PRIVATE" not in str(ev.after) and "PRIVATE" not in ev.summary

    j = db.session.get(AgentJob, admin.post(f"/api/hosts/{DNS}/ssh-keys/apply").get_json()["id"])
    assert j.params["break_glass"] == {"user": "admin", "key": out["public_key"]}
    assert j.params["users"] == {"admin": [], "deploy": []}

    _report(a, glass=out["sha256"])
    assert admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()["break_glass_state"] == "ok"
    pasted = ed_key("offline")
    r = admin.put(f"/api/hosts/{DNS}/ssh-keys/break-glass", json={"user": "deploy", "public_key": pasted})
    assert r.status_code == 200 and "private_key" not in r.get_json() and r.get_json()["source"] == "pasted"
    assert admin.get(f"/api/hosts/{DNS}/ssh-keys").get_json()["break_glass_state"] == "not_on_host"


def test_who_may_do_what(hub, make_user):
    a = _agent(hub)
    editor = make_user("ed", role="editor")
    assert editor.get(f"/api/hosts/{DNS}/ssh-keys").status_code == 200
    assert editor.get("/api/ssh-keys").status_code == 403
    assert editor.post("/api/ssh-keys", json={"name": "x", "public_key": ed_key()}).status_code == 403
    assert editor.post(f"/api/hosts/{DNS}/ssh-keys/apply").status_code == 403
    assert editor.put(f"/api/hosts/{DNS}/ssh-keys/break-glass", json={"user": "admin", "generate": True}).status_code == 403
    # an editor may ask for a report (read-only), never send an apply of their own making
    assert editor.post(f"/api/hosts/{DNS}/ssh-keys/report").status_code == 201
    r = editor.post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "ssh_keys.apply", "params": {"users": {"admin": [ed_key()]}}})
    assert r.status_code == 409 and "SSH keys page" in r.get_json()["error"]
    r = hub["admin"].post(f"/api/hosts/{DNS}/agent/jobs", json={"type": "ssh_keys.apply", "params": {"users": {}}})
    assert r.status_code == 409
    # ssh_keys must be on at both ends
    a.capabilities = []
    db.session.commit()
    r = hub["admin"].post(f"/api/hosts/{DNS}/ssh-keys/apply")
    assert r.status_code == 409 and "switch on ssh_keys" in r.get_json()["error"]
    assert hub["admin"].patch(f"/api/hosts/{DNS}/agent", json={"capabilities": ["ssh_keys"]}).status_code == 200
    assert editor.patch(f"/api/hosts/{DNS}/agent", json={"capabilities": []}).status_code == 403


def test_the_hub_checks_apply_params_too(hub):
    a = _agent(hub)
    for bad in ({"users": {"admin": ["not a key"]}}, {"users": {"Root!": []}}, {"users": {}, "path": "/root"},
                {"users": {}, "break_glass": {"user": "admin"}}, {"users": ["admin"]}):
        with pytest.raises(agents.AgentError):
            agents.queue(a, "ssh_keys.apply", bad)
    db.session.rollback()


def test_hello_and_the_schedule(hub):
    a = _agent(hub, able=False)
    agents.hello(a, {"jobs": ["ssh_keys.report", "nope"], "ssh_users": ["admin", "../root", 7], "allow": ["ssh_keys", "x"]}, "w1")
    assert a.ssh_users == ["admin"] and a.jobs == ["ssh_keys.report"] and a.host_allow == ["ssh_keys"]
    db.session.commit()
    agents.tick()
    db.session.commit()
    assert db.session.scalar(db.select(AgentJob).where(AgentJob.type == "ssh_keys.report", AgentJob.trigger == "schedule"))
    # the Python agent doesn't run it: no report is queued for it
    b = _agent(hub)
    assert "ssh_keys.report" not in agents.protocol.JOB_TYPES and "ssh_keys" not in agents.protocol.CAPABILITIES
    assert b.id
