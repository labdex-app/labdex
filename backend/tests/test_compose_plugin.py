"""Compose stacks as code: stacks kept in labdex, planned against the stack folders on the Docker
hosts, written with Ansible only after a person approves. Ansible itself is faked: each fake host
is a dict of stack folders, and nothing reaches a real host."""
import base64
import json
from pathlib import Path

import pytest
import yaml

from labdex import composefiles
from labdex.plugins.base import AnsibleResult, Context

from .test_plugins import make_instance

DOCKER, MON = "srv-vm-dns-01", "srv-vm-mon-01"  # example hosts of the test inventory

STACKS = f"""
- name: grafana
  host: {DOCKER}
  compose:
    services:
      grafana:
        image: grafana/grafana:11.3.0
        ports: ["3000:3000"]
        environment: {{GF_SECURITY_ADMIN_USER: "${{GF_ADMIN_USER}}"}}
  env:
    GF_ADMIN_USER: admin
    GF_ADMIN_PASSWORD: {{$secret: "vault:apps/grafana#admin"}}
- name: whoami
  host: {MON}
  compose: {{services: {{whoami: {{image: "traefik/whoami:v1.10"}}}}}}
"""


# ------------------------------------------------------------------ the stack model
def test_parse_validates_every_field():
    stacks = composefiles.parse(STACKS)
    assert [(s["host"], s["name"]) for s in stacks] == [(DOCKER, "grafana"), (MON, "whoami")]
    with pytest.raises(composefiles.StackError) as e:
        composefiles.parse("- {name: Bad Name, compose: {}}\n"
                           "- {name: a, host: h, compose: {services: {x: {}}}, env: {1BAD: x, OK: {vault: y}, ML: \"a\\nb\"}}\n"
                           "- {name: a, host: h, compose: {services: {x: {}}}}")
    fields = {x["field"] for x in e.value.errors}
    assert {"stacks[0].name", "stacks[0].host", "stacks[0].compose", "stacks[1].env.1BAD", "stacks[1].env.OK",
            "stacks[1].env.ML", "stacks[2].name"} <= fields


def test_plan_only_touches_folders_labdex_wrote():
    grafana, whoami = composefiles.parse(STACKS)
    written = {"marker": composefiles.marker(whoami), "compose": composefiles.compose_text(whoami)}
    seen = {f"{MON}/whoami": written,
            f"{DOCKER}/old": {"marker": {"managed_by": "labdex", "stack": "old"}, "compose": "services: {}\n"},
            f"{DOCKER}/hand": {"marker": None, "compose": "services: {}\n"}}
    ops = {f"{o['host']}/{o['name']}": o for o in composefiles.plan([grafana, whoami], seen)}
    assert ops[f"{DOCKER}/grafana"]["op"] == "create" and "+    image: grafana/grafana:11.3.0" in ops[f"{DOCKER}/grafana"]["diff"]
    assert f"{MON}/whoami" not in ops                                       # unchanged
    assert ops[f"{DOCKER}/old"]["op"] == "keep"                            # removing is off by default
    assert f"{DOCKER}/hand" not in ops                                      # not ours, not wanted: left alone
    assert composefiles.plan([], seen, allow_remove=True)[0]["op"] == "delete"

    # a folder of the same name that labdex didn't write is a conflict, never overwritten
    clash = {f"{DOCKER}/grafana": {"marker": None, "compose": "services: {}\n"}}
    assert composefiles.plan([grafana], clash)[0]["op"] == "conflict"

    # the env is compared by fingerprint of its references: a new secret reference is an update
    changed = dict(grafana, env={**grafana["env"], "GF_ADMIN_PASSWORD": {"$secret": "vault:apps/grafana#other"}})
    have = {f"{DOCKER}/grafana": {"marker": composefiles.marker(grafana), "compose": composefiles.compose_text(grafana)}}
    (op,) = composefiles.plan([changed], have)
    assert op["op"] == "update" and op["env_changed"] and op["diff"] == "" and ".env changed" in op["summary"]


def test_env_file_is_taken_literally_by_compose():
    text = composefiles.env_text({"A": "plain", "B": "it's $HOME", "C": True, "D": "two\nlines", "E": "a\\b\"c"})
    assert text.splitlines() == ["A='plain'", 'B="it\'s \\$HOME"', "C='true'", 'D="two\\nlines"', 'E=\'a\\b"c\'']


# ------------------------------------------------------------------ through the plugin
@pytest.fixture
def hosts(monkeypatch):
    """The fake Docker hosts: host → {stack folder → {marker, compose, env}}; runs records every Ansible run."""
    fs = {DOCKER: {}, MON: {}}
    runs = []

    def fake(self, playbook=None, *, extravars=None, limit=None, check=False, diff=False, inventory_override=None,
             base_dir=None, roles_path=None, inline=None):
        names = limit.split(",")
        runs.append({"play": inline, "hosts": sorted(inventory_override["all"]["hosts"]), "limit": names, "extravars": extravars})
        tasks = inline[0]["tasks"]
        if inline[0]["name"] == "labdex: read the stacks":
            dest = tasks[1]["ansible.builtin.copy"]["dest"]
            for h in names:
                out = {n: {"marker": f.get("marker"), "compose": f.get("compose")} for n, f in fs[h].items()}
                Path(dest.replace("{{ inventory_hostname }}", h)).write_text(json.dumps(out))
            return AnsibleResult(0, 0, 0, 0, "")
        for item in extravars["labdex_items"]:
            folders = fs[item["host"]]
            if item["op"] == "delete":
                if (folders.get(item["name"]) or {}).get("marker"):
                    del folders[item["name"]]
            else:
                folders[item["name"]] = {"marker": item["marker"], "compose": base64.b64decode(item["compose_b64"]).decode(),
                                         "env": base64.b64decode(item["env_b64"]).decode()}
        return AnsibleResult(0, len(extravars["labdex_items"]), 0, 0, "")
    monkeypatch.setattr(Context, "ansible", fake)
    return fs, runs


@pytest.fixture
def compose(vault, hosts):
    admin = vault
    r = admin.post("/api/vault/entries", json={"path": "apps/grafana", "fields": {"admin": "correct-horse-example"}})
    assert r.status_code == 201, r.get_json()
    make_instance(admin, "compose", "stacks", {"stacks": STACKS, "ssh_user": "deploy"})
    # anyone may apply anything right away: the plugin must still wait for a person
    assert admin.post("/api/approval-policies", json={"name": "no approvals", "approvals": 0}).status_code == 201
    return admin


def deploy(c, **params):
    r = c.post("/api/integrations/stacks/actions/deploy", json={"params": params})
    assert r.status_code == 202, r.get_json()
    return c.get(f"/api/jobs/{r.get_json()['id']}").get_json()


def test_plan_waits_for_a_person_then_writes_the_stacks(compose, hosts):
    fs, runs = hosts
    job = deploy(compose)
    assert job["status"] == "planned" and job["needed"] == 1, job
    assert sorted((c["op"], c["name"]) for c in job["plan"]) == [("create", f"{DOCKER}/grafana"), ("create", f"{MON}/whoami")]
    assert "correct-horse-example" not in json.dumps(job)                   # the plan never holds a secret value
    assert len(runs) == 1 and runs[0]["play"][0]["tasks"][0]["changed_when"] is False  # planning only read
    assert fs == {DOCKER: {}, MON: {}}

    compose.post(f"/api/jobs/{job['id']}/approve", json={"comment": "ok"})
    done = compose.get(f"/api/jobs/{job['id']}").get_json()
    assert done["status"] == "applied", done
    assert sorted(done["result"]["deployed"]) == [f"{DOCKER}/grafana", f"{MON}/whoami"]
    stack = fs[DOCKER]["grafana"]
    assert stack["compose"].startswith(composefiles.HEADER)
    assert yaml.safe_load(stack["compose"])["services"]["grafana"]["image"] == "grafana/grafana:11.3.0"
    assert stack["env"] == "GF_ADMIN_USER='admin'\nGF_ADMIN_PASSWORD='correct-horse-example'\n"
    assert stack["marker"]["managed_by"] == "labdex"
    play = runs[-1]["play"][0]
    env_task = next(t for t in play["tasks"] if t["name"] == ".env")
    assert env_task["no_log"] is True and env_task["ansible.builtin.copy"]["mode"] == "0600"
    assert "correct-horse-example" not in json.dumps(play)                  # values travel as data, never as playbook text
    assert runs[-1]["hosts"] == [DOCKER, MON]

    # nothing changed: nothing to plan
    again = deploy(compose)
    assert again["status"] == "succeeded" and not again["plan"], again


def test_hand_written_folder_stops_the_plan(compose, hosts):
    fs, _ = hosts
    fs[DOCKER]["grafana"] = {"marker": None, "compose": "services: {grafana: {image: grafana/grafana:10}}\n"}
    job = deploy(compose)
    assert job["status"] == "failed" and "folders labdex didn't write" in job["result"]["error"], job
    assert fs[DOCKER]["grafana"]["marker"] is None


def test_removing_a_stack_needs_the_setting(compose, hosts):
    fs, runs = hosts
    compose.post(f"/api/jobs/{deploy(compose)['id']}/approve", json={})
    only_grafana = "\n".join(STACKS.split("- name: whoami")[0].splitlines())
    compose.put("/api/integrations/stacks", json={"settings": {"stacks": only_grafana}})
    job = deploy(compose)
    assert not job.get("plan"), job                                         # whoami keeps running
    assert "whoami" in fs[MON]

    compose.put("/api/integrations/stacks", json={"settings": {"allow_remove": True}})
    job = deploy(compose)
    assert [(c["op"], c["name"]) for c in job["plan"]] == [("delete", f"{MON}/whoami")]
    compose.post(f"/api/jobs/{job['id']}/approve", json={})
    assert compose.get(f"/api/jobs/{job['id']}").get_json()["result"]["removed"] == [f"{MON}/whoami"]
    assert fs[MON] == {}


def test_only_some_stacks_and_checked_settings(compose, hosts):
    fs, runs = hosts
    job = deploy(compose, stacks=["whoami"])
    assert [c["name"] for c in job["plan"]] == [f"{MON}/whoami"] and runs[-1]["limit"] == [MON]
    job = deploy(compose, stacks=["nope"])
    assert job["status"] == "failed" and "no stack nope" in job["result"]["error"]
    compose.put("/api/integrations/stacks", json={"settings": {"root": "/etc"}})
    job = deploy(compose)
    assert job["status"] == "failed" and "absolute path of its own" in job["result"]["error"]
    compose.put("/api/integrations/stacks", json={"settings": {"root": "/opt/stacks",
                                                               "stacks": STACKS.replace("#admin", "#missing")}})
    job = deploy(compose)
    assert job["status"] == "failed" and "not in the vault" in job["result"]["error"], job
