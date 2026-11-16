"""Pulumi engine: the hosts of a site, kept at Contabo and Cloudflare through Pulumi's Automation API.

The resources are made from the inventory by labdex.iac.public; labdex.iac.engine runs them as
an inline program with Pulumi's file backend under labdex's data folder (one stack per
integration). Plans are `pulumi preview`, applying is `pulumi up`.
"""
from __future__ import annotations

from labdex.iac import EngineError, engine, public
from labdex.iac.contabo import Client, ContaboError
from labdex.plugins.base import Change, Plugin as Base, PluginError

CLOUDFLARE_PLUGIN = {"cloudflare": ""}  # provider plugin, installed by the CLI on first use ('' = the SDK's version)


class Plugin(Base):
    # --------------------------------------------------------------- helpers
    def _resources(self):
        res, notes = public.resources(self.ctx.hosts(), self.settings)
        for n in notes:
            self.log(n)
        return res

    def _engine(self) -> engine.Engine:
        s = self.settings
        env = {}
        if s.get("contabo"):
            env.update({"CONTABO_CLIENT_ID": s.get("contabo_client_id"), "CONTABO_CLIENT_SECRET": s.get("contabo_client_secret"),
                        "CONTABO_API_USER": s.get("contabo_api_user"), "CONTABO_API_PASSWORD": s.get("contabo_api_password")})
        if s.get("cloudflare"):
            env["CLOUDFLARE_API_TOKEN"] = s.get("cloudflare_api_token")
        stack = self.ctx.site or self.ctx.name
        try:
            return engine.Engine(project=f"labdex-{self.ctx.name}", stack=stack, state_dir=self.ctx.data_dir / "pulumi" / self.ctx.name,
                                 passphrase=s.get("state_passphrase") or "", env=env)
        except EngineError as exc:
            raise PluginError(str(exc)) from exc

    def _contabo(self) -> Client:
        s = self.settings
        if not all(s.get(k) for k in ("contabo_client_id", "contabo_client_secret", "contabo_api_user", "contabo_api_password")):
            raise PluginError("fill in the four Contabo credentials first")
        return Client(s["contabo_client_id"], s["contabo_client_secret"], s["contabo_api_user"], s["contabo_api_password"])

    # --------------------------------------------------------------- actions
    def test(self):
        ok, why = engine.available()
        out = ["Pulumi: ready" if ok else f"Pulumi: {why}"]
        if self.settings.get("contabo"):
            try:
                n = len(self._contabo().instances())
                out.append(f"Contabo: signed in, {n} instance(s)")
            except ContaboError as exc:
                raise PluginError(f"Contabo: {exc}") from exc
        if self.settings.get("cloudflare"):
            http = self.ctx.http("https://api.cloudflare.com/client/v4", headers={"Authorization": f"Bearer {self.settings.get('cloudflare_api_token', '')}"})
            zone = self.settings.get("cloudflare_zone_id") or ""
            r = http.get(f"zones/{zone}").json()
            out.append(f"Cloudflare: zone {(r.get('result') or {}).get('name', zone)} reachable")
        if not ok:
            raise PluginError("; ".join(out))
        return "; ".join(out)

    def run_resources(self, params):
        res = self._resources()
        self.log(f"site {self.ctx.site or '(every site)'}: {len(res)} resource(s)")
        for r in res:
            self.log(f"  {r.type:<18} {r.name}" + (f"  (adopts {r.import_id})" if r.import_id else "") + ("  [protected]" if r.protect else ""))
        return {"resources": [r.as_dict() for r in res]}

    def plan_up(self, params):
        res = self._resources()
        if not res:
            self.log("nothing to manage: turn on Contabo or Cloudflare, and check the hosts this integration applies to")
        try:
            steps = self._engine().preview(res, self.log)
        except EngineError as exc:
            raise PluginError(str(exc)) from exc
        return [Change(**c) for c in engine.steps_to_changes(steps)]

    def apply_up(self, changes, params):
        try:
            out = self._engine().up(self._resources(), self.log)
        except EngineError as exc:
            raise PluginError(str(exc)) from exc
        self.ctx.set_state("outputs", out.get("outputs") or {})
        new_ips = {k.split(".")[0]: v for k, v in (out.get("outputs") or {}).items() if k.endswith(".ip_v4") and v}
        missing = [h for h in new_ips if not any(i.get("ip4") for i in (self.ctx.host(h) or {}).get("interfaces", []))]
        if missing:
            self.log("add these addresses to the inventory: " + ", ".join(f"{h} {new_ips[h]}" for h in missing))
        return {"summary": out.get("summary"), "new_addresses": {h: new_ips[h] for h in missing}}

    def run_refresh(self, params):
        try:
            return self._engine().refresh(self._resources(), self.log)
        except EngineError as exc:
            raise PluginError(str(exc)) from exc

    # ------------------------------------------------------------- endpoints
    def endpoint_instances(self, args):
        """Instances at Contabo, to adopt existing nodes (copy an id into a host's settings)."""
        try:
            rows = self._contabo().instances()
        except ContaboError as exc:
            raise PluginError(str(exc)) from exc
        known = {str((h.get("module_settings") or {}).get("instance_id") or ""): h["name"] for h in self.ctx.hosts(include_offline=True)}
        return [{"instance_id": str(r.get("instanceId", "")), "name": r.get("displayName") or r.get("name", ""),
                 "ip4": ((r.get("ipConfig") or {}).get("v4") or {}).get("ip", ""), "product": r.get("productId", ""),
                 "region": r.get("region", ""), "status": r.get("status", ""), "host": known.get(str(r.get("instanceId", "")), "")} for r in rows]

    # ------------------------------------------------------------- host panel
    def host_view(self, host: dict) -> dict:
        """What Pulumi keeps for this host, and what the last apply reported (no provider calls)."""
        h = self.ctx.host(host["name"])
        if h is None:
            return {"sections": [{"type": "text", "title": "Not managed", "text": "This integration doesn't apply to the host (its site or selectors)."}]}
        res, notes = public.resources([h], self.settings)
        outs = self.ctx.state.get("outputs") or {}
        rows = [{"type": r.type.replace(":", " "), "name": r.name,
                 "adopts": r.import_id, "protected": "yes" if r.protect else "",
                 "last": ", ".join(f"{k.split('.', 1)[1]} {v}" for k, v in outs.items() if k.startswith(r.name + ".") and v)}
                for r in res]
        sections = [{"title": "Resources", "type": "table",
                     "columns": [{"key": "type", "label": "Type"}, {"key": "name", "label": "Name"}, {"key": "adopts", "label": "Adopts"},
                                 {"key": "protected", "label": "Protected"}, {"key": "last", "label": "Last apply"}],
                     "rows": rows}]
        if notes:
            sections.append({"title": "Notes", "type": "text", "text": "\n".join(notes)})
        return {"summary": [{"label": "resources", "value": len(rows), "tone": "ok" if rows else ""}], "sections": sections}
