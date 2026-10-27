from labdex.plugins.base import Plugin as Base


class Plugin(Base):
    def _hosts(self):
        statuses = set(self.settings.get("statuses") or ["active"])
        zones = set(self.settings.get("zones") or [])
        for h in self.ctx.inventory.hosts():
            if h["status"] in statuses and (not zones or h["zone"] in zones):
                yield h

    def _labels(self, h):
        return {"host": h["name"], "fun": h.get("fun") or h["name"], "zone": h["zone"],
                "area": h["zone"].split("_")[0], "function": h.get("function", ""), "role": h.get("function", ""), "status": h["status"]}

    def endpoint_nodes(self, args):
        port = int(self.settings.get("exporter_port") or 9100)
        domain = self.ctx.inventory.settings()["domain"]
        out = []
        for h in self._hosts():
            ifaces = [i for i in h["interfaces"] if i.get("ip4") or i.get("ip6_all")]
            primary = next((i for i in ifaces if i.get("primary")), ifaces[0] if ifaces else None)
            if primary is None:
                continue
            if self.settings.get("use_dns") and primary.get("network"):
                addr = f"{h['name']}.{primary['network']}.{domain}"
            else:
                addr = primary.get("ip4") or f"[{primary['ip6_all'][0]}]"
            out.append({"targets": [f"{addr}:{port}"], "labels": self._labels(h)})
        return out

    def endpoint_services(self, args):
        from labdex.models import Host, db
        from labdex.store import service_url
        out = []
        names = {h["name"] for h in self._hosts()}
        for host in db.session.scalars(db.select(Host).where(Host.name.in_(names))):
            for s in host.services:
                url = service_url(host, s)
                if url:
                    labels = self._labels({"name": host.name, "fun": host.fun, "zone": host.zone,
                                           "function": host.function, "role": host.function, "status": host.status})
                    out.append({"targets": [url], "labels": {**labels, "service": s.name}})
        return out
