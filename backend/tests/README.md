# Tests

Run with `pytest -q` from `backend/` (see CONTRIBUTING.md for PostgreSQL, the browser tests and
the performance budget).

Older files are named after the release that added the feature (`test_v1240.py` is 1.24: its
docstring says what it covers, and CHANGELOG.md has the story). **New tests are named after the
feature** (`test_saved_searches.py`), so you can find them without knowing the release. This index
groups both by area.

| Area | Files |
| --- | --- |
| Inventory, naming, IPAM, generators, YAML | `test_core`, `test_v051`–`test_v059`, `test_v0100`–`test_v0105`, `test_v1110`, `test_v1160`, `test_v170` |
| Lint | `test_core` (basics), `test_lint_rules` |
| Zones and policy | `test_v090`, `test_v091`, `test_v092`, `test_v1220` |
| Cables, racks, topology | `test_v1100`, `test_v1140`, `test_v1170`, `test_v1200`, `test_v1240`, `test_labels` |
| History, notifications, digest | `test_history`, `test_history_digest`, `test_v062` |
| Hosts page: search, bulk, saved searches | `test_v160`, `test_v190`, `test_saved_searches` |
| Users, roles, sign-in | `test_auth`, `test_rbac` |
| Vault and secrets | `test_v03`, `test_v063`, `test_v1150`, `test_v1180` |
| Jobs, plans, approvals, the worker | `test_v060`, `test_v061` |
| Plugins: loading, trust, isolation, Marketplace | `test_plugins`, `test_v04`, `test_v093`, `test_v100`, `test_v130`, `test_v150`, `test_v1210`, `test_v1230`, `test_v1241` |
| Plugin test harness (`labdex.testing`) | `test_plugin_harness` |
| Official plugins (against fake servers) | `test_v0104`, `test_v071`–`test_v075`, `test_v080`–`test_v084`, `test_v110`, `test_v140`, `test_contracts` |
| Dashboards | `test_v064`, `test_v1120` |
| Importing from other tools | `test_importers` |
| API description | `test_openapi` |
| Demo mode | `test_v094` |
| Upgrades and performance | `test_upgrade_gate`, `test_perf` |
| Browser (opt-in: `LABDEX_E2E=1`) | `e2e/` |

`fakes.py` has small fake OPNsense and Technitium servers; plugin authors get a general one in
`labdex.testing` (see *Testing* in docs/develop/plugins.md).
