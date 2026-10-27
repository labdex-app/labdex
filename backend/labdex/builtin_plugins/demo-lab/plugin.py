"""A pretend DNS server and monitor for demo mode. Nothing leaves labdex: applied records
are kept in the integration's state, host status is made up (one host has a bad day)."""
from __future__ import annotations

import hashlib
import math
import time

from labdex.plugins.base import Change, Plugin as Base


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


class Plugin(Base):
    # ------------------------------------------------------------ DNS sync
    def _want(self) -> dict:
        return {f"{r['fqdn']} {r['type']} {r['value']}": {"fqdn": r["fqdn"], "type": r["type"], "value": r["value"]}
                for r in self.ctx.inventory.dns_records()}

    def plan_sync(self, params):
        have = self.ctx.state.get("records") or {}
        want = self._want()
        out = [Change("create", "record", k, f"{v['type']} {v['value']}", None, v, v) for k, v in sorted(want.items()) if k not in have]
        out += [Change("delete", "record", k, f"{v['type']} {v['value']}", v, None, v) for k, v in sorted(have.items()) if k not in want]
        self.log(f"{len(want)} records in the inventory, {len(have)} on the pretend server")
        return out

    def apply_sync(self, changes, params):
        have = dict(self.ctx.state.get("records") or {})
        for c in changes:
            if c.op == "create":
                have[c.name] = c.data
            else:
                have.pop(c.name, None)
            self.log(f"{c.op} {c.name}: ok (demo: nothing left labdex)")
        self.ctx.set_state("records", have)
        return {"applied": len(changes)}

    # ---------------------------------------------------------- host status
    def _sick(self) -> str:
        """The host having a bad day changes every few hours."""
        names = sorted(h["name"] for h in self.ctx.inventory.hosts() if h.get("status") == "active" and h.get("kind") != "vip")
        return names[(int(time.time()) // 10800) % len(names)] if names else ""

    def run_poll(self, params):
        sick = self._sick()
        res = {}
        for h in self.ctx.inventory.hosts():
            if h.get("status") != "active" or h.get("kind") == "vip":
                continue
            down = h["name"] == sick
            res[h["name"]] = {"state": "down" if down else "up", "detail": "no answer for 3 checks" if down else "answering"}
        out = self.ctx.report_status(res)
        return {"message": f"{len(res)} hosts, {sum(1 for r in res.values() if r['state'] == 'down')} down", "changes": len(out["changes"])}

    # -------------------------------------------------------------- widgets
    def widget_traffic(self, host):
        now = int(time.time()) // 300
        pts = [round(40 + 25 * math.sin((now - i) / 9) + (_h(str(now - i)) % 12), 1) for i in range(48)][::-1]
        return {"kind": "sparkline", "points": pts, "value": pts[-1], "unit": "Mbit/s", "sub": "last 4 hours (made up)"}

    def widget_records(self, host):
        have, want = self.ctx.state.get("records") or {}, self._want()
        missing = len(set(want) - set(have))
        return {"kind": "stat", "value": len(have), "unit": "records", "tone": "warn" if missing else "ok",
                "sub": f"{missing} waiting for a sync" if missing else "in sync with the inventory"}

    def widget_load(self, host):
        base = _h(host["name"]) % 40
        now = int(time.time()) // 300
        pts = [round(base + 20 * abs(math.sin((now - i + base) / 7)), 1) for i in range(36)][::-1]
        return {"kind": "sparkline", "points": pts, "value": pts[-1], "unit": "%", "min": 0, "max": 100, "sub": "CPU, last 3 hours (made up)"}

    # ----------------------------------------------------------------- page
    def page_lab(self, args):
        have = self.ctx.state.get("records") or {}
        return {"summary": [{"label": "records on the pretend server", "value": len(have), "tone": ""},
                            {"label": "host having a bad day", "value": self._sick() or "—", "tone": "warn"}],
                "sections": [{"title": "Records", "type": "table", "columns": [{"key": "fqdn", "label": "Name"}, {"key": "type", "label": "Type"},
                                                                              {"key": "value", "label": "Value"}],
                              "rows": sorted(have.values(), key=lambda r: (r["fqdn"], r["type"]))[:200], "empty": "Nothing synced yet."}],
                "note": "This is the demo. The DNS server and the monitor are pretend; nothing here talks to a network."}
