"""VictoriaMetrics / Prometheus: host status from `up`, CPU and memory for the host page."""
from __future__ import annotations

import time

from labdex.plugins.base import Plugin as Base
from labdex.plugins.base import PluginError


class Plugin(Base):
    def _client(self):
        s = self.settings
        if not s.get("url"):
            raise PluginError("set the URL first")
        headers, auth = {}, None
        if s.get("auth") == "basic":
            auth = (s.get("username") or "", s.get("password") or "")
        elif s.get("auth") == "bearer":
            headers["Authorization"] = f"Bearer {s.get('token') or ''}"
        return self.ctx.http(s["url"].rstrip("/") + "/", verify=bool(s.get("verify_tls", True)), auth=auth, headers=headers, timeout=10)

    def _query(self, q: str) -> list[dict]:
        r = self._client().get("api/v1/query", params={"query": q})
        try:
            data = r.json()
        except ValueError as exc:
            raise PluginError(f"not a Prometheus API answer (HTTP {r.status_code})") from exc
        if data.get("status") != "success":
            raise PluginError(f"query failed: {data.get('error') or data}")
        return data["data"]["result"]

    def _range(self, q: str, hours: int = 6, step: int = 300) -> list[dict]:
        end = int(time.time())
        r = self._client().get("api/v1/query_range", params={"query": q, "start": end - hours * 3600, "end": end, "step": step})
        data = r.json()
        if data.get("status") != "success":
            raise PluginError(f"query failed: {data.get('error') or data}")
        return data["data"]["result"]

    def test(self):
        res = self._query(self.settings.get("up_query") or "up")
        return f"{len(res)} targets answer the status query"

    # --------------------------------------------------------------- status
    def _targets(self):
        label = self.settings.get("match_label") or "instance"
        match = self.ctx.match_host()
        out = []
        for r in self._query(self.settings.get("up_query") or "up"):
            m = r.get("metric", {})
            target = m.get(label, "")
            try:
                val = float(r["value"][1])
                ts = float(r["value"][0])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            out.append({"host": match(target), "target": target, "job": m.get("job", ""), "up": val >= 1, "ts": ts})
        return out

    def run_poll(self, params):
        results: dict[str, dict] = {}
        unmatched = 0
        for t in self._targets():
            if t["host"] is None:
                unmatched += 1
                continue
            r = results.setdefault(t["host"], {"up": 0, "down": [], "jobs": set()})
            r["jobs"].add(t["job"] or t["target"])
            if t["up"]:
                r["up"] += 1
            else:
                r["down"].append(t["job"] or t["target"])
        report = {}
        for host, r in results.items():
            if r["down"] and not r["up"]:
                report[host] = {"state": "down", "detail": "every target down: " + ", ".join(sorted(r["down"]))}
            elif r["down"]:
                report[host] = {"state": "up", "detail": f"{r['up']} up, down: " + ", ".join(sorted(r["down"]))}
            else:
                report[host] = {"state": "up", "detail": f"{r['up']} target{'s' if r['up'] != 1 else ''} up"}
        out = self.ctx.report_status(report)
        self.log(f"{len(report)} hosts reported, {unmatched} targets match no host")
        return {"hosts": len(report), "unmatched": unmatched, "changes": len(out["changes"])}

    # -------------------------------------------------------------- widgets
    def widget_targets(self, host):
        down = [t for t in self._targets() if not t["up"]]
        items = [{"label": t["host"] or t["target"], "state": "down", "detail": t["job"],
                  "link": f"/hosts/{t['host']}" if t["host"] else ""} for t in down[:20]]
        return {"kind": "status", "items": items, "sub": "every target answers" if not down else f"{len(down)} target(s) down"}

    def _host_series(self, q: str, host: dict) -> list[float]:
        if not q:
            return []
        label = self.settings.get("match_label") or "instance"
        match = self.ctx.match_host()
        for series in self._range(q):
            if match(series.get("metric", {}).get(label, "")) == host["name"]:
                return [round(float(v), 1) for _, v in series.get("values", [])]
        return []

    def widget_host(self, host):
        cpu = self._host_series(self.settings.get("cpu_query", ""), host)
        mem = self._host_series(self.settings.get("mem_query", ""), host)
        if not cpu and not mem:
            return {"kind": "list", "items": [], "sub": "no CPU or memory series for this host"}
        pts = cpu or mem
        what = "CPU" if cpu else "memory"
        sub = f"{what} %, last 6 h" + (f" · memory {mem[-1]:.0f} %" if cpu and mem else "")
        return {"kind": "sparkline", "points": pts, "value": pts[-1], "unit": "%", "min": 0, "max": 100, "sub": sub}
