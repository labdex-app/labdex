# Configuration

Everything is set with environment variables; conventions (domain, alias length,
categories, the DMZ area) are edited in the UI under Settings → Conventions.

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
| `LABDEX_OIDC_ISSUER` | | Sign in with OpenID Connect, see below |
| `LABDEX_COOKIE_SECURE` | `false` | `true` behind HTTPS |
| `LABDEX_SESSION_HOURS` | `12` | Session lifetime |
| `LABDEX_SCHEDULER` | `true` | Run scheduled actions and templates |
| `LABDEX_WORKER` | `inline` | `external`: only queue jobs for `labdex worker` |
| `LABDEX_DEMO` | `false` | [Demo mode](demo.md). Wipes the database: never on a real instance |
| `LABDEX_DEV_PLUGINS` | | Development only: a folder of plugin checkouts (the repository's `plugins/`) loaded as official plugins. Never on a real instance |

## Single sign-on with OpenID Connect

labdex signs people in with Authentik, Keycloak, Authelia, Pocket ID, Zitadel or any other
OpenID Connect provider (authorization code flow with PKCE). Create an application there
with the redirect URI `https://<labdex>/api/auth/oidc/callback` (Settings › Users shows the
exact one), then:

| Variable | Default | |
| --- | --- | --- |
| `LABDEX_OIDC_ISSUER` | | The issuer URL, e.g. `https://auth.example.net/application/o/labdex/` |
| `LABDEX_OIDC_CLIENT_ID` | | |
| `LABDEX_OIDC_CLIENT_SECRET` | | Empty for a public client (PKCE alone) |
| `LABDEX_OIDC_NAME` | `single sign-on` | The button: "Sign in with Authentik" |
| `LABDEX_OIDC_SCOPES` | `openid profile email groups` | |
| `LABDEX_OIDC_USERNAME_CLAIM` | `preferred_username` | The labdex user name (else the email's local part) |
| `LABDEX_OIDC_GROUPS_CLAIM` | `groups` | Matched to labdex groups by name (Keycloak's `/a/b` paths too) |
| `LABDEX_OIDC_ADMIN_GROUPS` | | Comma separated: members are administrators (and only they) |
| `LABDEX_OIDC_AUTOCREATE` | `true` | A first sign-in creates the account |
| `LABDEX_OIDC_ONLY` | `false` | Hide the password form (setting up the first administrator still works) |

An account is linked to the provider's subject at its first sign-in: an existing local
account with the same user name is taken over once, after that a rename at the provider
changes nothing. Group memberships follow the provider at every sign-in. Set the public URL
(Settings › General) when labdex sits behind a proxy, so the redirect URI is right.

## SQLite or PostgreSQL

SQLite is right for almost every homelab. For PostgreSQL install the `postgres`
extra (the image has it) and move an existing instance over:

```sh
labdex db copy --to postgresql://labdex:pw@db/labdex   # then set LABDEX_DB and restart
labdex db current                                     # schema revision
```

The schema is migrated on start; see [Upgrading](../operate/upgrading.md).
