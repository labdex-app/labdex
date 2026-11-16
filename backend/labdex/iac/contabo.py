"""Contabo compute instances: a small API client and a Pulumi dynamic provider on top of it.

There is no Contabo package for Pulumi on PyPI, so instances are a dynamic resource: Pulumi
keeps their state, this module talks to https://api.contabo.com. The credentials come from the
environment the engine sets for the run (CONTABO_CLIENT_ID, CONTABO_CLIENT_SECRET,
CONTABO_API_USER, CONTABO_API_PASSWORD), never from the stack's state.

Deleting an instance means *cancelling* it at Contabo (it runs to the end of its term). The
provider only does that when the resource says `allow_cancel`; otherwise a delete just forgets the
instance, and labdex protects instances by default anyway.
"""
from __future__ import annotations

import os
import uuid

AUTH_URL = "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
API_URL = "https://api.contabo.com/v1"
ENV = ("CONTABO_CLIENT_ID", "CONTABO_CLIENT_SECRET", "CONTABO_API_USER", "CONTABO_API_PASSWORD")
# what an instance's inputs may change in place; anything else of these means a new instance
IN_PLACE = {"display_name"}
REPLACES = {"product_id", "region"}
REINSTALL = {"image_id", "ssh_keys", "user_data"}  # a reinstall wipes the disk: shown as a replace


class ContaboError(Exception):
    pass


class Client:
    def __init__(self, client_id: str, client_secret: str, user: str, password: str, *, api_url: str = "", auth_url: str = "",
                 session=None, timeout: int = 30):
        import requests
        self.creds = (client_id, client_secret, user, password)
        self.api = (api_url or os.environ.get("CONTABO_API_URL") or API_URL).rstrip("/")
        self.auth = auth_url or os.environ.get("CONTABO_AUTH_URL") or AUTH_URL
        self.s = session or requests.Session()
        self.timeout = timeout
        self._token = ""

    @classmethod
    def from_env(cls) -> Client:
        missing = [k for k in ENV if not os.environ.get(k)]
        if missing:
            raise ContaboError(f"missing Contabo credentials: {', '.join(missing)}")
        return cls(*(os.environ[k] for k in ENV))

    def token(self) -> str:
        if not self._token:
            cid, secret, user, password = self.creds
            r = self.s.post(self.auth, data={"client_id": cid, "client_secret": secret, "username": user, "password": password,
                                             "grant_type": "password"}, timeout=self.timeout)
            if r.status_code != 200:
                raise ContaboError(f"Contabo sign-in failed (HTTP {r.status_code}); check the API client and user")
            self._token = r.json()["access_token"]
        return self._token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token()}", "x-request-id": str(uuid.uuid4())}
        r = self.s.request(method, f"{self.api}/{path.lstrip('/')}", json=body, headers=headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise ContaboError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    # ------------------------------------------------------------- instances
    def instances(self) -> list[dict]:
        out, page = [], 1
        while True:
            d = self.call("GET", f"/compute/instances?page={page}&size=100")
            out += d.get("data") or []
            pages = ((d.get("_pagination") or {}).get("totalPages")) or 1
            if page >= pages:
                return out
            page += 1

    def instance(self, instance_id) -> dict | None:
        try:
            d = self.call("GET", f"/compute/instances/{instance_id}")
        except ContaboError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        rows = d.get("data") or []
        return rows[0] if rows else None

    def create(self, props: dict) -> dict:
        body = {"imageId": props["image_id"], "productId": props["product_id"], "region": props.get("region") or "EU",
                "displayName": props.get("display_name") or "", "period": int(props.get("period") or 1)}
        if props.get("ssh_keys"):
            body["sshKeys"] = [int(k) for k in props["ssh_keys"]]
        if props.get("user_data"):
            body["userData"] = props["user_data"]
        rows = self.call("POST", "/compute/instances", body).get("data") or []
        if not rows:
            raise ContaboError("Contabo accepted the order but returned no instance")
        return rows[0]

    def rename(self, instance_id, display_name: str) -> None:
        self.call("PATCH", f"/compute/instances/{instance_id}", {"displayName": display_name})

    def cancel(self, instance_id) -> None:
        self.call("POST", f"/compute/instances/{instance_id}/cancel", {})


def outputs(row: dict, props: dict) -> dict:
    """The state Pulumi keeps for an instance: its inputs plus what Contabo reports."""
    ip4 = ((row.get("ipConfig") or {}).get("v4") or {}).get("ip", "")
    ip6 = ((row.get("ipConfig") or {}).get("v6") or {}).get("ip", "")
    return {**props, "instance_id": str(row.get("instanceId", "")), "ip_v4": ip4 or "", "ip_v6": ip6 or "",
            "status": row.get("status", ""), "display_name": row.get("displayName", props.get("display_name", "")),
            "product_id": row.get("productId", props.get("product_id", "")), "region": row.get("region", props.get("region", "")),
            "image_id": row.get("imageId", props.get("image_id", ""))}


def diff(olds: dict, news: dict) -> tuple[list[str], list[str]]:
    """(changed keys, keys that need a new instance) between the stored state and new inputs."""
    keys = IN_PLACE | REPLACES | REINSTALL
    changed = sorted(k for k in keys if k in news and str(olds.get(k, "")) != str(news.get(k, "")) and news.get(k) not in (None, "", []))
    # an adopted instance has no image/ssh keys in its inputs: only what was asked for counts
    replaces = [k for k in changed if k in REPLACES or (k in REINSTALL and olds.get(k) not in (None, "", []))]
    return changed, replaces
