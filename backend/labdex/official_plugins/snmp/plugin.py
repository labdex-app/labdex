"""SNMP: neighbours from LLDP-MIB and CISCO-CDP-MIB, suggested as cables, and whether each switch answers.

Every host this applies to (tag:snmp to start with) is asked, at its own address or its primary
one, for its name, its interface names, and its LLDP and CDP tables. What it reports goes to
labdex's cabling (record_links): ends are matched to hosts, and the Cables page suggests the
cables the inventory doesn't have yet.
"""
from __future__ import annotations

from labdex import lldp, snmp
from labdex.plugins.base import Plugin as Base
from labdex.plugins.base import PluginError

UPTIME = "1.3.6.1.2.1.1.3.0"


class Plugin(Base):
    def _client(self, address: str) -> snmp.Client:
        s = self.settings
        port, timeout = int(s.get("port") or 161), float(s.get("timeout") or 3)
        if (s.get("version") or "v2c") == "v3":
            if not s.get("user"):
                raise PluginError("set the SNMPv3 user first")
            auth = "" if (s.get("auth") or "sha") == "none" else s.get("auth") or "sha"
            priv = "" if not auth or (s.get("priv") or "aes") == "none" else "aes"
            try:
                v3 = snmp.V3Auth(s["user"], auth, s.get("auth_pass") or "", priv, s.get("priv_pass") or "")
            except snmp.SnmpError as exc:
                raise PluginError(str(exc)) from None
            if auth and len(s.get("auth_pass") or "") < 8:
                raise PluginError("the authentication passphrase needs at least 8 characters (SNMPv3 asks for that)")
            if priv and len(s.get("priv_pass") or "") < 8:
                raise PluginError("the privacy passphrase needs at least 8 characters (SNMPv3 asks for that)")
            return snmp.Client(address, port, v3=v3, timeout=timeout, retries=1)
        if not s.get("community"):
            raise PluginError("set the community first")
        return snmp.Client(address, port, community=s["community"], timeout=timeout, retries=1)

    def _targets(self) -> list[tuple[dict, str]]:
        out = []
        for h in self.ctx.hosts():
            addr = (h.get("module_settings") or {}).get("address")
            if not addr:
                prim = next((i for i in h.get("interfaces", []) if i.get("primary")), None) or next(iter(h.get("interfaces") or []), None)
                addr = (prim or {}).get("ip4") or (prim or {}).get("ip6") or ""
            out.append((h, addr))
        if not out:
            raise PluginError("this applies to no host yet: tag your switches snmp, or change what it applies to")
        return out

    def test(self):
        ok, bad = [], []
        for h, addr in self._targets()[:20]:
            if not addr:
                bad.append(f"{h['name']}: no address")
                continue
            try:
                with self._client(addr) as c:
                    name = lldp.text(c.get(lldp.SYS_NAME).get(lldp.SYS_NAME))
                ok.append(f"{h['name']} ({name or 'no name'})")
            except snmp.SnmpError as exc:
                bad.append(f"{h['name']}: {exc}")
        if not ok:
            raise PluginError("; ".join(bad))
        return f"{len(ok)} answer: " + ", ".join(ok) + (f". Not: {'; '.join(bad)}" if bad else "")

    def _read(self, c: snmp.Client) -> list[dict]:
        def walk(name):
            try:
                return c.walk(lldp.WALKS[name])
            except snmp.SnmpError:
                return []  # an agent without that MIB
        names = lldp.if_names(walk("if_name"), walk("if_descr"))
        found = lldp.lldp(walk("lldp_rem"), walk("lldp_loc"), walk("lldp_man"), names)
        return found + lldp.cdp(walk("cdp"), names)

    def run_poll(self, params):
        links, report, errors, per_host = [], {}, [], {}
        for h, addr in self._targets():
            if not addr:
                errors.append(f"{h['name']}: no address")
                continue
            try:
                with self._client(addr) as c:
                    up = c.get(lldp.SYS_NAME, UPTIME)
                    found = self._read(c)
            except snmp.SnmpError as exc:
                errors.append(f"{h['name']}: {exc}")
                report[h["name"]] = {"state": "down", "detail": str(exc)[:200]}
                continue
            days = int(up.get(UPTIME) or 0) // 8640000
            report[h["name"]] = {"state": "up", "detail": f"SNMP answers · up {days} d · {len(found)} neighbour(s)",
                                 "data": {"sys_name": lldp.text(up.get(lldp.SYS_NAME)), "neighbours": len(found)}}
            per_host[h["name"]] = [{k: n[k] for k in ("port", "protocol", "name", "remote_port", "ip", "mac", "platform")} for n in found]
            for n in found:
                links.append({"host": h["name"], "port": n["port"], "protocol": n["protocol"], "remote_port": n["remote_port"],
                              "remote": {"name": n["fqdn"] or n["name"], "ip": n["ip"], "mac": n["mac"], "platform": n["platform"]}})
        for e in errors:
            self.ctx.log(e)
        seen = self.ctx.discovery.record_links(f"snmp {self.ctx.name}", links)
        self.ctx.report_status(report)
        self.ctx.set_state("neighbours", per_host)
        if errors and not per_host:
            raise PluginError("no host answered: " + "; ".join(errors[:5]))
        return {"switches": len(per_host), "links": seen["links"], "matched_ends": seen["matched_ends"], "errors": len(errors)}

    def widget_host(self, host):
        rows = (self.ctx.state.get("neighbours") or {}).get(host["name"])
        if rows is None:
            return {"kind": "list", "items": [], "sub": "not read yet (Read neighbours)"}
        return {"kind": "table", "columns": ["Port", "Neighbour", "Its port", "Via"],
                "rows": [[r["port"], r["name"] or r["ip"] or r["mac"] or "?", r["remote_port"], r["protocol"].upper()] for r in rows][:60],
                "sub": f"{len(rows)} neighbour(s)"}
