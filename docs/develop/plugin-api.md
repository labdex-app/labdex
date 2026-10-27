# Plugin API reference

This is the stable plugin API of labdex 1.x: within a major version it only grows.
Anything not listed here (other modules, models, private methods) may change.

## plugin.yaml

| Key | |
| --- | --- |
| `id` | lowercase letters, digits, dashes |
| `name`, `version`, `description`, `author`, `homepage`, `tags` | shown in the UI |
| `kind` | `integration` (instances) or `module` (one) |
| `labdex` | PEP 440 range of labdex versions, e.g. `">=1.0,<2"` |
| `settings`, `host_settings` | fields: `key`, `label`, `type` (`string secret bool int select text list networks table yaml`), `required`, `default`, `help`, `placeholder`, `options` (select), `columns` (table) |
| `actions` | `id`, `label`, `mode` (`run plan export`), `description`, `scope` (`global host`), `params`, `schedulable`, `schedule_default` (minutes), `danger`, `ansible` (playbook path) |
| `hooks` | `events` (globs: `host.*`, `network.*`, `alias.*`, `policy.updated`, …), `action` |
| `pages` | `id`, `label`: pages, shown as tabs on the integration |
| `widgets` | `id`, `label`, `where` (`dashboard host`), `size` (`small wide`), `refresh` (seconds) |
| `endpoints` | `id`, `label`: JSON for other tools |
| `host_view` | `true`: a panel on host pages |
| `provides` | `discovery`, `features` |
| `python` | file with `class Plugin` (default `plugin.py`) |

## Isolated plugins

Community and unsigned plugins run in a separate, locked-down process (see
[Installing plugins](../plugins/installing.md#isolation)). For them:

- `self.ctx` offers exactly the Context below; labdex's other modules aren't reachable
  (importing `labdex.store` works, but it has no database to talk to).
- `data_dir` is the instance's own folder, kept between runs; the working directory and
  `TMPDIR` are a fresh temporary folder per call.
- Everything you pass to labdex or return goes over JSON: keep return values to dicts,
  lists, strings, numbers, `Change` and `ExportFile`.
- `print()` ends up in the job log.
- Each call is a fresh process: keep state in `set_state`, not in module globals.
- Calls time out after an hour (actions) or a minute (pages, widgets, endpoints).

Write plugins that only use this page, and they run the same either way.

## Plugin

```python
class Plugin(labdex.plugins.base.Plugin):
    ctx: Context
    settings: dict                      # the instance's settings, secrets decrypted
    def log(self, msg): ...
```

| Method | Returns | For |
| --- | --- | --- |
| `run_<action>(params)` | dict (`message` is shown) | `mode: run` |
| `plan_<action>(params)` | `list[Change]` | `mode: plan` |
| `apply_<action>(changes, params)` | dict | applying a reviewed plan |
| `export_<action>(params)` | `ExportFile` | `mode: export` |
| `page_<id>(args)` | view | the integration's page tabs; a page with `place: policy` in the manifest is shown on Network › Policy › On the firewalls instead, with its `action` (e.g. `{id: policy, label: Firewall policy, place: policy, action: sync_rules}`) |
| `host_view(host)` | view | host panel |
| `widget_<id>(host)` | widget | `host` is `None` on the dashboard |
| `endpoint_<id>(args)` | JSON | endpoints |

`params` of host actions carry `host`.

## Context (`self.ctx`)

| | |
| --- | --- |
| `inventory` | an `InventoryView` (below) |
| `hosts(include_offline=False)` | hosts this instance applies to, each with `module_settings` |
| `host(name)` | one of them, or `None` |
| `http(base_url, *, verify, ca_file, timeout, auth, headers)` | an HTTP client (`get`, `post`, `request`) with the TLS settings applied; errors become `PluginError` |
| `ansible(playbook, *, extravars, limit, check, diff, …)` | runs a playbook; an `AnsibleResult` (`ok`, `rc`, `changed`, `failed`, `unreachable`, `output`) |
| `state`, `set_state(key, value)` | small private state kept on the instance |
| `report_status({host: {state, detail, data}})` | host status from this source (`up`, `down`, `unknown`) |
| `match_host()` | a function: address, name, URL or `host:port` → host name |
| `notify(event, title, body="", link="", data=None)` | a notification (`plugin.<id>.<event>`; `host.down`/`host.up` as is) |
| `discovery.record_scan(source, scanned, hosts)` | a port scan: `hosts` = `[{ip, hostname, mac, services: [{port, proto, service, product, version}]}]`; ports not found on scanned addresses are marked closed |
| `discovery.record_neighbours(source, entries, mark_up=True)` | ARP/DHCP/DNS style data: `[{ip, mac, hostname, vendor}]` |
| `discovery.record_macs(source, entries)` | A switch's forwarding table, replacing what `source` said before: `[{host or switch: {name, ip, mac}, port, mac, vlan, ip, name}]`; returns `{macs}`. Feeds Discovery › Where is (since 1.24; check with `hasattr`) |
| `discovery.record_links(source, links)` | Links a switch sees, replacing what `source` said before: `[{host or device: {name, ip, mac}, port, remote: {name, ip, mac, platform}, remote_port, protocol}]`; returns `{links, matched_ends}`. They become suggested cables |
| `data_dir` | a folder for caches: the instance's own when isolated, labdex's data folder otherwise (also callable: `data_dir()`) |
| `log(message)` | a line in the job log |

## InventoryView (`self.ctx.inventory`)

Read-only: `hosts(include_offline=False)`, `networks()`, `aliases()` (generated and
custom, as the firewall sees them), `custom_aliases()`, `dns_records()`, `dns_zones()`,
`dns_zone()` (zone file), `dhcp_reservations()`, `ansible_inventory()`, `yaml()`,
`settings()`, `features()`. The one write: `apply_delegated_prefixes(observed, source)`.

## Change and ExportFile

```python
Change(op, kind, name, summary="", before=None, after=None, data={})
# op: create | update | delete | ansible | other; data is what apply_ needs
ExportFile(filename, content, mimetype)
```

## Views (pages and host panels)

```python
{"summary": [{"label": "…", "value": 3, "tone": "ok|warn|err|"}],
 "sections": [{"title": "…", "type": "table", "columns": [{"key": "name", "label": "Name", "link": "host"}],
               "rows": [{"name": "…", "_tone": "warn"}], "empty": "…"},
              {"title": "…", "type": "list", "items": ["…"]},
              {"title": "…", "type": "kv", "items": {"key": "value"}},
              {"title": "…", "type": "text", "text": "…"}],
 "note": "…"}
```

## Widgets

`stat` (`value`, `unit`, `tone`), `status` (`items`: `label`, `state` up/down/warn/
unknown, `detail`), `list` (`items`: `label`, `value`, `tone`), `table` (`columns`,
`rows`), `sparkline` (`points`, `value`, `unit`, `min`, `max`). Each can have `sub` and
`link`. labdex draws them: no plugin code runs in the browser.

## Events

Hooks see inventory events: `host.created|updated|deleted`, `network.created|updated|deleted`,
`alias.created|updated|deleted`, `feature.updated|deleted`, `hostmodule.updated`,
`setting.updated`, `inventory.imported`, `policy.updated`.

Notifications add `plan.waiting`, `plan.applied`, `job.failed`, `backup.failed`,
`host.down`, `host.up` and `plugin.<id>.<event>`.
