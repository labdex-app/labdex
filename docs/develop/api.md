# REST API

An OpenAPI 3 description of every route is at `/api/openapi.json` (when signed in) and from
`labdex openapi -o labdex-api.json`; point a client generator, Postman or Swagger UI at it.
It is built from the routes themselves, so it lists exactly what the running version has.
Request and response bodies are plain JSON objects there; the table below says what they hold.

JSON under `/api`; sign in with the session cookie (browser; writes also need
the header `X-Requested-With: labdex`) or a bearer token. Validation errors come
back as `422` with a list of `{field, message}`; missing permissions as `403`;
objects outside your scope as `404`.

Every successful `GET` carries a weak `ETag`; send it back as `If-None-Match`
and an unchanged answer is an empty `304`. Lists page with a cursor:
`/api/history` and `/api/jobs` take `limit` and `before=<id>` and return the
next cursor in `X-Next-Before`. `/api/hosts` takes `fields=name,zone,…`, `q=`,
`zone=`, `status=`, `sort=name|-zone|ip|…`, `limit=` and `offset=`, with the
total in `X-Total-Count`.

Hosts, networks and zones carry a `uid` (ten characters, never changes); every path that
takes a host or network name (`/api/hosts/<name>`, `/api/networks/<name>`, …) takes the
`uid` as well, so scripts that keep ids survive renames.

| Method | Path | |
| --- | --- | --- |
| GET | `/api/meta` | Zones, areas, settings, counts, your permissions |
| GET, POST · GET, PUT, DELETE | `/api/hosts` · `/api/hosts/<name>` | Hosts (filtered to what you may see; `can_edit` per row) |
| GET, POST · GET, PUT, DELETE | `/api/networks` · `/api/networks/<name>` | Networks; `/next` gives the next free address |
| GET, POST · GET, PUT, DELETE | `/api/dashboards` · `/api/dashboards/<uid>` | Dashboards: `{name, shared, items: [{type, x, y, w, h, config}]}`; shared ones need an administrator |
| GET, POST · GET, PUT, DELETE | `/api/racks` · `/api/racks/<name or uid>` | Racks with the hosts in them; `PUT`/`DELETE /api/racks/<rack>/hosts/<host>` `{unit, height, face}` places or takes out a host; `POST /api/racks/<rack>/items`, `PUT`/`DELETE …/items/<id>` `{kind, label, description, unit, height, face}` for panels |
| GET · POST | `/api/vault` · `/api/vault/{init,unseal,seal,passphrase}` | The vault's state (`initialized`, `sealed`, `reauth_needed`); setting it up, unsealing, sealing, a new passphrase (administrators) |
| GET, POST · GET, PUT, DELETE | `/api/vault/entries?host=&q=` · `/api/vault/entries/<uid>` | Secrets by name: `{path, host, description, fields: {name: value}}`; values are never in these answers. In `PUT`, a field's value `null` keeps it, fields left out go |
| POST | `/api/vault/entries/<uid>/reveal` · `/api/auth/confirm` | `{field, version?}` → `{value}`, needs `secret:apply`, written to History; a browser session confirms its password first (`{password}`, every 5 minutes, else `403` with field `password`) |
| GET · POST · POST | `/api/cables/suggestions` · `/api/cables/suggestions/accept` · `/api/cables/suggestions/ignore` | Cables switches see: `suggested`, `conflicts` (with what's `now` on record), `unknown` neighbours; accept `{keys, replace}`, ignore `{keys}` |
| GET | `/api/cables` | Every cable you may see both ends of, with `label`, `length` (metres), `colour`; and `next_label` |
| GET, POST · PUT, DELETE | `/api/topology/views` · `/api/topology/views/<uid>` | Saved topology views: `{name, config: {view, zone, hidden_nets, lonely, grid, camera: {zoom, x, y}}}` |
| GET | `/api/zones/zones/<code or uid>` | One zone: its networks, hosts at home there, policy cells from and to it |
| GET, PUT · POST, PUT, DELETE | `/api/topology?view=physical\|l3` · `/api/topology/layout`, `/api/topology/cables[/<id>]` | The pictures, their shared arrangement, cables drawn on the map (both ends may be one host, on different ports); `PUT …/cables/<id>` `{medium, speed, notes}` changes one |
| GET, POST, PUT, DELETE | `/api/aliases[/<name>]` | Aliases, generated and custom |
| GET | `/api/services`, `/api/lint` | Services, convention checks (accepted ones carry `accepted`: the reason) |
| POST, DELETE | `/api/lint/accept` | Accept a finding `{code, kind, ref, reason}` / count it again (administrators) |
| GET · GET · POST | `/api/history` · `/api/history/<id>` · `/api/history/<id>/revert` | Audit log with diffs |
| GET, POST · PUT, DELETE | `/api/searches` · `/api/searches/<uid>` | Saved Hosts searches, yours and shared ones |
| POST | `/api/hosts/bulk` | One change to many hosts: status, runs on, tags, features, zone, integration, delete |
| GET | `/api/hosts/<name>/label.svg` · `/api/cables/label.svg?host=&port=` | Printable labels with a QR code (`size=62x29`, `50x25`, `89x36`) |
| POST | `/api/admin/import/external` | Add networks and hosts from NetBox, phpIPAM or CSV (`apply: false` previews) |
| GET | `/api/history/changes?since=7d` | Net change per object since a point in time (`24h`, `2w`, a date; `until` optional) |
| GET | `/api/discovery/{summary,services,missing,unknown-hosts}` | Discovery |
| GET, POST · GET, PUT, DELETE | `/api/integrations` · `/api/integrations/<name>` | Integration instances |
| POST | `/api/integrations/<name>/actions/<action>` | Start a job (`202`) |
| GET | `/api/integrations/<name>/export/<action>`, `/endpoints/<id>` | Exports, plugin endpoints |
| GET · POST | `/api/jobs[/<id>]` · `/api/jobs/<id>/{apply,discard}` | Jobs and plans |
| GET · PUT | `/api/hosts/<name>/modules` · `/api/hosts/<name>/modules/<integration>` | Integrations for a host: on/off, host settings |
| GET · POST | `…/modules/<integration>/view` · `…/actions/<action>` | Host panel, host actions |
| GET, POST · PUT, DELETE | `/api/features` · `/api/features/<path>` | Feature tree |
| GET · POST, PUT, DELETE | `/api/zones` · `/api/zones/{areas,trust}[/<code>]` | Areas and trust levels; `POST /api/zones/example` |
| GET · GET · POST, PUT, DELETE | `/api/naming` · `/api/naming/next?category=&type=&role=` · `/api/naming/<kind>[/<code>]` | Hostname parts, next free name |
| GET, POST · PUT, DELETE · GET | `/api/naming/pools` · `/api/naming/pools/<name>` · `/api/naming/fun-suggest?category=&zone=&pool=` | Fun name pools, unused suggestions |
| GET | `/api/about` | Version, schema, database, plugins, changelog |
| GET | `/api/nav` | Sidebar: zone tree with live status, which pages to show |
| GET | `/api/events[?job=<id>]` | Server-Sent Events: plan count, `revision` on every change, job status and output |
| POST · POST · DELETE | `/api/plugins/upload` · `/api/plugins/stage/<token>/confirm` · `/api/plugins/<id>` | Install, confirm, remove |
| GET · POST | `/api/plugin-repos` (`/preview`) | Signed repositories |
| GET | `/api/marketplace` (`?offline=1` skips the repositories) | Everything installable: what repositories offer (`installed`, `update`, `repo_version`), and development checkouts (`bundled`) |
| POST | `/api/marketplace/install` `{repository_id, id}` | Stages a repository plugin for `/api/plugins/stage/<token>/confirm` (a development checkout, `{id}`, installs at once) |
| DELETE | `/api/plugins/<id>` | Uninstall (409 while it has instances) |
| POST | `/api/plugins/restore` | Install the plugins this labdex uses but doesn't have (after an upgrade from 1.24.0) from the official repository now: `{installed, failed, missing}`; `GET /api/plugins` lists them as `missing` |
| POST · DELETE | `/api/discovery/addresses/assign` · `/api/discovery/addresses/<ip>` | Add a seen address to a host; forget it |
| GET · PUT, DELETE | `/api/secrets` · `/api/secrets/<name>` | The older API for vault entries under `global/` (field `value`); writing needs the vault unsealed |
| GET | `/api/vault/refs` | The fields you may point plugins and Ansible variables at: `vault:<path>#<field>` |
| GET | `/api/dashboard`, `/api/widgets`, `/api/hosts/<name>/widgets` | Start page, widgets |
| GET · PUT | `/api/zone-policy` | Zone matrix (PUT one cell: `{src, dst, ports, description}`; admins) |
| POST | `/api/jobs/<id>/approve`, `/api/jobs/<id>/discard` | `{comment}` |
| GET, POST, PUT, DELETE | `/api/approval-policies`, `/api/change-windows` | Approvals and windows (`PUT /api/approval-policies/order`) |
| GET, POST, PUT, DELETE | `/api/notification-channels` (`/<id>/test`), `/api/notifications` | Notifications |
| GET, POST, PUT, DELETE | `/api/job-templates` (`/<id>/run`) | Job templates |
| GET | `/api/plugins/<id>`, `/api/jobs/queue` | Plugin details, the job queue |
| | `/api/auth/*`, `/api/admin/*` | Sign-in, tokens; users, groups, roles, bindings, grants, backup/restore |

Within 1.x, fields and endpoints are added, not removed or renamed
([Upgrading](../operate/upgrading.md#within-1x)).

## Change sets

`GET/POST /api/changesets`, `GET/PUT/DELETE /api/changesets/<uid>`, `POST /api/changesets/<uid>/preview` and `/apply` (422 with the report when a change is refused). A body is `{name, description, yaml}`.
