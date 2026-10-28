"""REST API under /api. Every route needs a logged-in principal except /api/auth/*."""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from .. import auth, store
from ..models import db
from ..plugins.base import PluginError

bp = Blueprint("api", __name__, url_prefix="/api")

PUBLIC = {"api.auth_status", "api.auth_login", "api.auth_setup", "api.auth_oidc_login", "api.auth_oidc_callback"}


@bp.before_request
def _authenticate():
    auth.load_principal()
    p = auth.g.principal
    if request.endpoint not in PUBLIC and p.user is None:
        return err(401, "login required")
    if not auth.csrf_ok():
        return err(403, "missing X-Requested-With header")
    from .. import demo
    return demo.guard()


def err(status: int, message: str, errors=None):
    body = {"error": message}
    if errors:
        body["errors"] = errors
    return jsonify(body), status


@bp.errorhandler(store.ValidationError)
def _validation(exc):
    db.session.rollback()
    return err(422, "validation failed", exc.errors)


@bp.errorhandler(PluginError)
def _plugin(exc):
    db.session.rollback()
    return err(409, str(exc))


@bp.errorhandler(HTTPException)
def _http(exc):
    return err(exc.code or 500, exc.description or exc.name)


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise store.ValidationError([{"field": "body", "message": "expected a JSON object"}])
    return data


def text_response(text: str, mimetype: str, filename: str):
    resp = Response(text, mimetype=mimetype)
    if request.args.get("download") or isinstance(text, bytes):
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# the route modules register themselves on bp when imported
from . import (  # noqa: E402, F401
    about_api, admin, auth_routes, dashboard_api, dashboards_api, discovery_api, dns_api, events_api, features_api, history,
    hostmodules_api, impact_api, inventory_api, nav_api, naming_api, operate_api, plugins_api, pluginstore_api, racks_api, search_api, secrets_api,
    tags_api, topology_api, vault_api, zones_api,
)
