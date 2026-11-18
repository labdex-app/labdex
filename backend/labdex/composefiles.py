"""Compose stacks as code: stacks kept in labdex, written to Docker hosts as compose files.

A stack:

    {name: grafana, host: srv-vm-docker-01, compose: {services: {grafana: {image: grafana/grafana:11.3.0, ...}}},
     env: {GF_ADMIN_USER: admin, GF_ADMIN_PASSWORD: {$secret: vault:apps/grafana#admin}}}

On the host it becomes <root>/<name>/compose.yaml, <root>/<name>/.env (mode 0600, secrets filled in
only while Ansible runs) and <root>/<name>/.labdex, a marker with the fingerprints labdex wrote.
Plans compare the compose file itself and the env by fingerprint, so secret values never leave
the vault for a plan. Only folders with a marker are ever changed or removed.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")  # a compose project name
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HEADER = "# Managed by labdex: edit this stack in labdex; changes made here are overwritten.\n"
SECRET = "$secret"


class StackError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


def parse(text: str) -> list[dict]:
    try:
        data = yaml.safe_load(text or "") or []
    except yaml.YAMLError as exc:
        raise StackError([{"field": "stacks", "message": f"not YAML: {str(exc).splitlines()[0]}"}]) from None
    if isinstance(data, dict):
        data = data.get("stacks") or []
    if not isinstance(data, list):
        raise StackError([{"field": "stacks", "message": "a list of stacks"}])
    return validate(data)


def validate(stacks: list) -> list[dict]:
    errors, out, seen = [], [], set()
    for i, s in enumerate(stacks):
        f = f"stacks[{i}]"
        if not isinstance(s, dict):
            errors.append({"field": f, "message": "a stack: {name, host, compose, env}"})
            continue
        name, host = str(s.get("name") or ""), str(s.get("host") or "")
        if not NAME_RE.match(name):
            errors.append({"field": f"{f}.name", "message": "lower-case letters, digits, - and _"})
        if (host, name) in seen:
            errors.append({"field": f"{f}.name", "message": f"'{name}' twice on {host}"})
        seen.add((host, name))
        if not host:
            errors.append({"field": f"{f}.host", "message": "the Docker host it runs on"})
        compose = s.get("compose")
        if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict) or not compose["services"]:
            errors.append({"field": f"{f}.compose", "message": "a compose file with services"})
            compose = {}
        env = s.get("env") or {}
        if not isinstance(env, dict):
            errors.append({"field": f"{f}.env", "message": "a mapping NAME: value"})
            env = {}
        for k, v in env.items():
            if not ENV_RE.match(str(k)):
                errors.append({"field": f"{f}.env.{k}", "message": "letters, digits and _"})
            if isinstance(v, dict) and set(v) != {SECRET}:
                errors.append({"field": f"{f}.env.{k}", "message": "a value or {$secret: vault:path#field}"})
            if isinstance(v, str) and "\n" in v:
                errors.append({"field": f"{f}.env.{k}", "message": "one line"})
        out.append({"name": name, "host": host, "compose": compose, "env": {str(k): v for k, v in env.items()}})
    if errors:
        raise StackError(errors)
    return out


def compose_text(stack: dict) -> str:
    return HEADER + yaml.safe_dump(stack["compose"], sort_keys=False)


def env_spec(stack: dict) -> str:
    """The env as labdex knows it: secret references, not values."""
    return json.dumps(stack["env"], sort_keys=True)


def env_text(values: dict) -> str:
    """The .env file for values with the secrets already filled in. Quoted so that compose takes each
    value literally: single quotes when possible, else double quotes with \\, \", \\$ and newlines escaped."""
    lines = []
    for k, v in values.items():
        v = "" if v is None else str(v).lower() if isinstance(v, bool) else str(v)
        if "'" not in v and "\n" not in v:
            lines.append(f"{k}='{v}'")
        else:
            esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("\n", "\\n")
            lines.append(f'{k}="{esc}"')
    return "".join(line + "\n" for line in lines)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def marker(stack: dict) -> dict:
    return {"managed_by": "labdex", "stack": stack["name"], "compose": fingerprint(compose_text(stack)), "env": fingerprint(env_spec(stack))}


def plan(stacks: list[dict], seen: dict[str, dict], allow_remove: bool = False) -> list[dict]:
    """What applying `stacks` changes, given what the hosts have:
    seen = {"host/name": {"marker": {...} | None, "compose": text | None}} for every stack folder found.
    → [{op: create|update|delete|conflict, host, name, summary, diff, env_changed}]"""
    out = []
    want = {f"{s['host']}/{s['name']}": s for s in stacks}
    for key in sorted(set(want) | set(seen)):
        s, have = want.get(key), seen.get(key) or {}
        host, name = key.split("/", 1)
        mk, old = have.get("marker"), have.get("compose")
        if s is None:
            if mk and mk.get("managed_by") == "labdex":
                if allow_remove:
                    out.append({"op": "delete", "host": host, "name": name, "summary": "docker compose down, then the folder is removed",
                                "diff": "", "env_changed": False})
                else:
                    out.append({"op": "keep", "host": host, "name": name, "summary": "no longer in labdex; left running (removing stacks is off)",
                                "diff": "", "env_changed": False})
            continue
        if old is not None and not mk:
            out.append({"op": "conflict", "host": host, "name": name, "diff": "", "env_changed": False,
                        "summary": "a stack labdex didn't write is in this folder; rename the stack or move it"})
            continue
        new = compose_text(s)
        env_changed = (mk or {}).get("env") != fingerprint(env_spec(s))
        if old == new and not env_changed:
            continue
        d = "".join(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True), f"a/{name}/compose.yaml", f"b/{name}/compose.yaml"))
        parts = []
        if old != new:
            added = sum(1 for x in d.splitlines() if x.startswith("+") and not x.startswith("+++"))
            removed = sum(1 for x in d.splitlines() if x.startswith("-") and not x.startswith("---"))
            parts.append(f"compose.yaml +{added} −{removed}")
        if env_changed and old is not None:
            parts.append(".env changed")
        out.append({"op": "create" if old is None else "update", "host": host, "name": name, "diff": d, "env_changed": env_changed,
                    "summary": ("new stack, " if old is None else "") + ", ".join(parts) + "; docker compose up -d"})
    return out
