"""Tailscale / Headscale: tailnet devices into Discovery, online state as host status."""
from __future__ import annotations

import datetime as dt

from labdex.plugins.base import Plugin as Base
from labdex.plugins.base import PluginError


def _age(ts: str) -> float | None:
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt.datetime.now(dt.UTC) - t).total_seconds()


class Plugin(Base):
    def devices(self) -> list[dict]:
        """[{name, hostname, addresses, os, online, last_seen, user, version, expires}]"""
        s = self.settings
        if s.get("flavor") == "headscale":
            if not s.get("url"):
                raise PluginError("set the Headscale URL first")
            http = self.ctx.http(s["url"].rstrip("/") + "/", timeout=10, headers={"Authorization": f"Bearer {s.get('api_key', '')}"})
            try:
                nodes = http.get("api/v1/node").json().get("nodes") or []
            except PluginError:
                nodes = http.get("api/v1/machine").json().get("machines") or []  # Headscale before 0.23
            return [{"name": n.get("givenName") or n.get("name", ""), "hostname": n.get("name", ""), "addresses": n.get("ipAddresses") or [],
                     "os": "", "online": bool(n.get("online")), "last_seen": n.get("lastSeen") or "",
                     "user": (n.get("user") or {}).get("name", ""), "version": "", "expires": n.get("expiry") or ""} for n in nodes]
        base = (s.get("url") or "https://api.tailscale.com").rstrip("/")
        http = self.ctx.http(base + "/api/v2/", timeout=10, headers={"Authorization": f"Bearer {s.get('api_key', '')}"})
        devs = http.get(f"tailnet/{s.get('tailnet') or '-'}/devices", params={"fields": "all"}).json().get("devices") or []
        out = []
        for d in devs:
            online = d.get("connectedToControl")
            if online is None:
                age = _age(d.get("lastSeen", ""))
                online = age is not None and age < 300
            out.append({"name": (d.get("name") or "").split(".")[0], "hostname": d.get("hostname", ""), "addresses": d.get("addresses") or [],
                        "os": d.get("os", ""), "online": bool(online), "last_seen": d.get("lastSeen", ""), "user": d.get("user", ""),
                        "version": (d.get("clientVersion") or "").split("-")[0], "expires": "" if d.get("keyExpiryDisabled") else d.get("expires", "")})
        return out

    def test(self):
        d = self.devices()
        return f"{len(d)} devices, {sum(1 for x in d if x['online'])} online"

    def _match(self, d, match):
        for a in d["addresses"]:
            h = match(a)
            if h:
                return h
        return match(d["hostname"]) or match(d["name"])

    def run_poll(self, params):
        from labdex import discovery
        devs = self.devices()
        entries_up = [{"ip": a, "hostname": d["name"] or d["hostname"]} for d in devs if d["online"] for a in d["addresses"]]
        entries_known = [{"ip": a, "hostname": d["name"] or d["hostname"]} for d in devs if not d["online"] for a in d["addresses"]]
        n = discovery.record_neighbours(f"tailscale {self.ctx.name}", entries_up)
        n += discovery.record_neighbours(f"tailscale {self.ctx.name}", entries_known, mark_up=False)
        match = self.ctx.match_host()
        report = {}
        offline_state = "down" if self.settings.get("offline") == "down" else "unknown"
        for d in devs:
            h = self._match(d, match)
            if h:
                report[h] = {"state": "up" if d["online"] else offline_state,
                             "detail": f"{d['name']} {'online' if d['online'] else 'offline'}" + (f" · {d['os']}" if d["os"] else ""),
                             "data": {"device": d["name"], "addresses": d["addresses"]}}
        self.ctx.report_status(report)
        self.ctx.set_state("devices", devs)
        return {"devices": len(devs), "online": sum(1 for d in devs if d["online"]), "matched": len(report), "addresses": n}

    def widget_devices(self, host):
        devs = self.ctx.state.get("devices")
        if devs is None:
            return {"kind": "list", "items": [], "sub": "run “Read devices”"}
        match = self.ctx.match_host()
        items = []
        for d in sorted(devs, key=lambda x: (not x["online"], x["name"])):
            h = self._match(d, match)
            items.append({"label": d["name"], "state": "up" if d["online"] else "unknown",
                          "detail": (d["addresses"][0] if d["addresses"] else "") + ("" if h else " · not in the inventory"),
                          "link": f"/hosts/{h}" if h else "/discovery"})
        return {"kind": "status", "items": items[:25], "sub": f"{sum(1 for d in devs if d['online'])} of {len(devs)} online"}

    def widget_host(self, host):
        match = self.ctx.match_host()
        for d in self.ctx.state.get("devices") or []:
            if self._match(d, match) == host["name"]:
                items = [{"label": "device", "value": d["name"]}, {"label": "addresses", "value": " ".join(d["addresses"])},
                         {"label": "online", "value": "yes" if d["online"] else "no", "tone": "ok" if d["online"] else "warn"}]
                if d.get("os"):
                    items.append({"label": "os", "value": f"{d['os']} {d.get('version', '')}".strip()})
                if d.get("expires"):
                    left = _age(d["expires"])
                    if left is not None:
                        days = -left / 86400
                        items.append({"label": "key expires", "value": f"in {days:.0f} d", "tone": "warn" if days < 14 else "ok"})
                return {"kind": "list", "items": items, "sub": d.get("user", "")}
        return {"kind": "list", "items": [], "sub": "not in the tailnet"}
