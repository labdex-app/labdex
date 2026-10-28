"""An OpenAPI 3 description of the REST API, built from the routes themselves.

Paths, methods, path parameters, who may call what (public or signed in) and each route's
docstring come from the code, so the description can't drift from the routes: a test fails
when a route is missing. Request and response bodies are described as free-form JSON objects;
the README and docs/develop/api.md say what they hold.
"""
from __future__ import annotations

import inspect
import re

from flask import Flask

_PARAM = re.compile(r"<(?:(?P<conv>[a-z]+)(?:\([^)]*\))?:)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>")
_TYPES = {"int": "integer", "float": "number", "uuid": "string", "path": "string", "string": "string", "any": "string"}
WRITE = {"POST", "PUT", "PATCH", "DELETE"}


def _path(rule: str) -> tuple[str, list[dict]]:
    params = []

    def sub(m):
        params.append({"name": m["name"], "in": "path", "required": True, "schema": {"type": _TYPES.get(m["conv"] or "string", "string")}})
        return "{" + m["name"] + "}"

    return _PARAM.sub(sub, rule), params


def _doc(fn) -> tuple[str, str]:
    doc = inspect.getdoc(fn) or ""
    head, _, rest = doc.partition("\n\n")
    summary = " ".join(head.split()) or fn.__name__.replace("_", " ").capitalize()
    return summary[:200], rest.strip()


def build(app: Flask) -> dict:
    from .api import PUBLIC
    paths: dict[str, dict] = {}
    used: set[str] = set()
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods or []))):
        if not rule.rule.startswith("/api/") or rule.endpoint == "static":
            continue
        path, params = _path(rule.rule)
        fn = app.view_functions[rule.endpoint]
        summary, description = _doc(fn)
        tag = path.split("/")[2]
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            op_id = f"{rule.endpoint.removeprefix('api.')}_{method.lower()}"
            while op_id in used:  # one view on several routes
                op_id += "_"
            used.add(op_id)
            op = {"operationId": op_id, "summary": summary, "tags": [tag],
                  "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}}
            if description:
                op["description"] = description
            if params:
                op["parameters"] = list(params)
            if rule.endpoint in PUBLIC:
                op["security"] = []
            else:
                op["responses"]["401"] = {"description": "Not signed in"}
                op["responses"]["403"] = {"description": "Your roles don't allow this"}
            if method in WRITE:
                op["responses"]["422"] = {"description": "Validation failed: {error, errors: [{field, message}]}"}
            if method in ("POST", "PUT", "PATCH"):
                op["requestBody"] = {"required": False, "content": {"application/json": {"schema": {"type": "object"}}}}
            paths.setdefault(path, {})[method.lower()] = op
    return {
        "openapi": "3.0.3",
        "info": {"title": "labdex API", "version": app.config.get("LABDEX_VERSION", ""),
                 "description": "Sign in with the session cookie (browsers; writes also need the header `X-Requested-With: labdex`) "
                                "or a bearer token (`Authorization: Bearer ldx_…`). Objects you may not see answer 404."},
        "servers": [{"url": "/"}],
        "security": [{"bearerAuth": []}, {"sessionCookie": []}],
        "components": {"securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer", "description": "An API token (Account › API tokens, or `labdex token create`)"},
            "sessionCookie": {"type": "apiKey", "in": "cookie", "name": app.config.get("SESSION_COOKIE_NAME", "labdex_session")}}},
        "paths": paths,
    }
