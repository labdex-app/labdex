"""NetBird: peers of a NetBird network into Discovery, their connection as host status.

Read-only: it only lists peers (GET /api/peers) with a service user's token."""
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
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - t).total_seconds()


class Plugin(Base):
    def _http(self):
        token = self.settings.get("token") or ""
        if not token:
            raise PluginError("set the access token first")
        base = (self.settings.get("url") or "https://api.netbird.io").rstrip("/")
        return self.ctx.http(base + "/api/", timeout=10, headers={"Authorization": f"Token {token}", "Accept": "application/json"})

    def peers(self) -> list[dict]:
        """[{name, hostname, dns, addresses, os, version, online, last_seen, groups, login_expired}]"""
        raw = self._http().get("peers").json()
        if not isinstance(raw, list):
            raise PluginError("unexpected answer from /api/peers (is this a NetBird management API?)")
        wanted = {g.lower() for g in self.settings.get("groups") or [] if g}
        out = []
        for p in raw:
            groups = sorted(g.get("name", "") for g in p.get("groups") or [] if g.get("name"))
            if wanted and not wanted & {g.lower() for g in groups}:
                continue
            addresses = [a for a in (p.get("ip"), p.get("ipv6")) if a]
            out.append({"name": p.get("name") or p.get("hostname") or "", "hostname": p.get("hostname") or "",
                        "dns": p.get("dns_label") or "", "addresses": addresses, "os": p.get("os") or "",
                        "version": p.get("version") or "", "online": bool(p.get("connected")),
                        "last_seen": p.get("last_seen") or "", "groups": groups,
                        "login_expired": bool(p.get("login_expired"))})
        return out

    def run_test(self, params):
        p = self.peers()
        return {"message": f"{len(p)} peers, {sum(1 for x in p if x['online'])} connected"}

    def _match(self, p, match):
        for a in p["addresses"]:
            h = match(a)
            if h:
                return h
        # dns_label is the full mesh name (eiger.node.netbird.cloud); the first label is often the host
        return match(p["hostname"]) or match(p["name"]) or (match(p["dns"].split(".")[0]) if p["dns"] else None)

    def run_poll(self, params):
        from labdex import discovery
        peers = self.peers()
        source = f"netbird {self.ctx.name}"
        up = [{"ip": a, "hostname": p["name"]} for p in peers if p["online"] for a in p["addresses"]]
        known = [{"ip": a, "hostname": p["name"]} for p in peers if not p["online"] for a in p["addresses"]]
        n = discovery.record_neighbours(source, up)
        n += discovery.record_neighbours(source, known, mark_up=False)
        match = self.ctx.match_host()
        offline_state = "down" if self.settings.get("offline") == "down" else "unknown"
        report = {}
        for p in peers:
            h = self._match(p, match)
            if h:
                report[h] = {"state": "up" if p["online"] else offline_state,
                             "detail": f"{p['name']} {'connected' if p['online'] else 'disconnected'}" + (f" · {p['os']}" if p["os"] else ""),
                             "data": {"peer": p["name"], "addresses": p["addresses"], "groups": p["groups"]}}
        self.ctx.report_status(report)
        self.ctx.set_state("peers", peers)
        return {"peers": len(peers), "connected": sum(1 for p in peers if p["online"]), "matched": len(report), "addresses": n}

    def widget_peers(self, host):
        peers = self.ctx.state.get("peers")
        if peers is None:
            return {"kind": "list", "items": [], "sub": "run “Read peers”"}
        match = self.ctx.match_host()
        items = []
        for p in sorted(peers, key=lambda x: (not x["online"], x["name"])):
            h = self._match(p, match)
            items.append({"label": p["name"], "state": "up" if p["online"] else "unknown",
                          "detail": (p["addresses"][0] if p["addresses"] else "") + ("" if h else " · not in the inventory"),
                          "link": f"/hosts/{h}" if h else "/discovery"})
        return {"kind": "status", "items": items[:25], "sub": f"{sum(1 for p in peers if p['online'])} of {len(peers)} connected"}

    def widget_host(self, host):
        match = self.ctx.match_host()
        for p in self.ctx.state.get("peers") or []:
            if self._match(p, match) != host["name"]:
                continue
            items = [{"label": "peer", "value": p["name"]}, {"label": "addresses", "value": " ".join(p["addresses"])},
                     {"label": "connected", "value": "yes" if p["online"] else "no", "tone": "ok" if p["online"] else "warn"}]
            if p["groups"]:
                items.append({"label": "groups", "value": ", ".join(p["groups"])})
            if p["os"]:
                items.append({"label": "os", "value": f"{p['os']} {p['version']}".strip()})
            if p["login_expired"]:
                items.append({"label": "login", "value": "expired", "tone": "warn"})
            if not p["online"] and (age := _age(p["last_seen"])) is not None:
                items.append({"label": "last seen", "value": f"{age / 3600:.0f} h ago"})
            return {"kind": "list", "items": items, "sub": p["dns"]}
        return {"kind": "list", "items": [], "sub": "not a NetBird peer"}
