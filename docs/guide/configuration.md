# Configuration

Everything is set with environment variables; conventions (domain, alias length,
categories, the DMZ area) are edited in the UI under Admin → Conventions.

| Variable | Default | |
| --- | --- | --- |
| `LABDEX_DATA` | `./data` (`/data` in the image) | Database, `secret.key`, `plugins/`, backups |
| `LABDEX_DB` | `sqlite:///$LABDEX_DATA/labdex.db` | or `postgresql://user:pw@host/db` |
| `LABDEX_SECRET_KEY` | generated into `$LABDEX_DATA/secret.key` | Signs sessions, encrypts secrets. Keep a copy apart from backups |
| `LABDEX_ADMIN_USER`, `LABDEX_ADMIN_PASSWORD` | | Create this administrator on an empty database |
| `LABDEX_PLUGIN_DIR` | `$LABDEX_DATA/plugins` | Installed plugins |
| `LABDEX_PLUGIN_SANDBOX` | `on` | Community and unsigned plugins run isolated (see [Installing plugins](../plugins/installing.md#isolation)). `strict`: only where the kernel has Landlock; `off`: inside labdex like official plugins |
| `LABDEX_SSO_HEADER` | | e.g. `Remote-User`, trusted only from `LABDEX_TRUSTED_PROXIES` |
| `LABDEX_SSO_GROUPS_HEADER` | | e.g. `Remote-Groups`, matched to labdex groups by name |
| `LABDEX_SSO_AUTOCREATE` | `false` | Create users on their first SSO visit |
| `LABDEX_TRUSTED_PROXIES` | | Proxy addresses allowed to send SSO headers |
| `LABDEX_COOKIE_SECURE` | `false` | `true` behind HTTPS |
| `LABDEX_SESSION_HOURS` | `12` | Session lifetime |
| `LABDEX_SCHEDULER` | `true` | Run scheduled actions and templates |
| `LABDEX_WORKER` | `inline` | `external`: only queue jobs for `labdex worker` |
| `LABDEX_DEMO` | `false` | [Demo mode](demo.md). Wipes the database: never on a real instance |

## SQLite or PostgreSQL

SQLite is right for almost every homelab. For PostgreSQL install the `postgres`
extra (the image has it) and move an existing instance over:

```sh
labdex db copy --to postgresql://labdex:pw@db/labdex   # then set LABDEX_DB and restart
labdex db current                                     # schema revision
```

The schema is migrated on start; see [Upgrading](../operate/upgrading.md).
