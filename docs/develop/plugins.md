# Writing a plugin

A plugin is a folder with `plugin.yaml` and, optionally, `plugin.py` and Ansible
playbooks. Drop it into `/data/plugins/<id>/` and restart (or Plugins → Reload).

```yaml
# plugin.yaml
id: pihole
name: Pi-hole
kind: integration            # or module (exists once, no instances to add)
version: "0.1"
labdex: ">=1.0,<2"           # the labdex versions it works with (PEP 440 range)
description: Local DNS records in Pi-hole
applies_to: ["tag:pihole"]   # what a new instance applies to (default *); RBAC scope syntax
settings:
  - {key: url, label: URL, type: string, required: true}
  - {key: password, type: secret, required: true}
  - {key: networks, type: networks}          # string secret bool int select text list networks table yaml
  - {key: auth, type: select, default: token, options: [{value: token, label: API token}, {value: password, label: Password}]}
  - {key: token, type: secret, required: true, when: {auth: token}}   # only shown (and required) for that choice
host_settings:                               # per host, shown on the host page
  - {key: records, type: table, columns: [{key: name}, {key: value, required: true}]}
host_view: true                              # host_view(host) fills a panel on the host page
tags: [dns]
provides: [discovery]                        # shows the Discovery page (or: features)
pages:                                       # tabs on the integration, filled by page_<id>(args)
  - {id: overview, label: Pi-hole}
actions:
  - {id: test, label: Test connection, mode: run}
  - {id: sync, label: Sync records, mode: plan, schedulable: true}
  - {id: hosts_file, label: hosts file, mode: export}
  - {id: deploy, mode: plan, ansible: playbooks/deploy.yml}   # Ansible: check+diff, then apply
  - {id: restart, mode: run, scope: host, danger: true,       # per host, with a form for parameters
     params: [{key: service, type: string, required: true}]}
hooks:
  - {events: ["host.*", "network.*"], action: sync}
endpoints:
  - {id: targets, label: JSON for something to poll}
widgets:
  - {id: queries, label: DNS queries, where: dashboard, size: wide, refresh: 60}
```

```python
# plugin.py
from labdex.plugins.base import Change, ExportFile, Plugin as Base, PluginError

class Plugin(Base):
    def run_test(self, params):
        self.ctx.http(self.settings["url"]).get("api/info")
        return {"message": "ok"}

    def plan_sync(self, params):           # a list of Change
        want = {r["fqdn"]: r for r in self.ctx.inventory.dns_records() if r["type"] == "A"}
        return [Change("create", "record", fqdn, f"A {r['value']}", after=r, data=r) for fqdn, r in want.items()]

    def apply_sync(self, changes, params):  # the reviewed plan
        for c in changes:
            self.log(f"creating {c.name}")
        return {"applied": len(changes)}

    def export_hosts_file(self, params):
        return ExportFile("hosts", "...", "text/plain")

    def endpoint_targets(self, params):
        return [...]

    def host_view(self, host):
        return {"summary": [{"label": "records", "value": 3, "tone": "ok"}],
                "sections": [{"title": "Records", "type": "table", "columns": [{"key": "name", "label": "Name"}],
                              "rows": [{"name": "nas", "_tone": "warn"}]}]}

    def widget_queries(self, host):         # host is None on the dashboard
        return {"kind": "sparkline", "points": [1, 3, 2], "unit": "q/s"}
```

Methods are found by name: `run_<action>`, `plan_<action>` and `apply_<action>`,
`export_<action>`, `endpoint_<id>`, `page_<id>`, `widget_<id>`, `host_view`.
Raise `PluginError("…")` for errors the user should read. The
[reference](plugin-api.md) lists everything a plugin can use.

## Testing

Run labdex locally (`labdex run --debug`) with `LABDEX_PLUGIN_DIR` pointing at your
plugins folder. labdex's own tests use small fake servers for each API
(`backend/tests/fakes.py`); the same pattern works for plugins.
