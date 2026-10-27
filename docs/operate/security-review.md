# Security review (1.0)

Before 1.0 the parts of labdex that face users, plugins and files were reviewed again.
This page records what was looked at, what changed, and what is left by design.

## Authentication

| Looked at | Result |
| --- | --- |
| Password hashing | scrypt with a per-user salt; a login for an unknown user costs the same as a real one |
| Brute force | Before: 5 failures per (address, user) in 5 minutes. Behind a reverse proxy every client had the proxy's address, so one person could lock a user out for everyone, and many addresses could try one user without limit. **Fixed**: the address is the client's (the last `X-Forwarded-For` hop that isn't a trusted proxy), and there is also a limit of 20 failures per user from anywhere. The table of failures can't grow without bound. |
| Sessions | HttpOnly, SameSite=Strict, `Secure` with `LABDEX_COOKIE_SECURE`; a password change ends every session (password version in the session) |
| CSRF | every write needs `X-Requested-With: labdex`, which no other site can send without a CORS preflight labdex never allows |
| API tokens | 256-bit random, stored as SHA-256, compared in constant time; optional read-only and expiry |
| SSO headers | only trusted from `LABDEX_TRUSTED_PROXIES`; users are created only with `LABDEX_SSO_AUTOCREATE` |
| Headers | CSP without inline scripts, no framing, `nosniff`, same-origin referrer. **Added**: `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and HSTS when cookies are secure. |

## Server-Sent Events

| Looked at | Result |
| --- | --- |
| Who may watch a job | checked when the stream opens (integration view right) |
| A stream outliving its session | **Fixed**: every 15 seconds the stream checks that the user is still active, the session's password unchanged and the API token still valid; otherwise it ends. |
| Exhausting server threads | **Fixed**: at most 8 open streams per user (429 beyond). |

## Plugins

| Looked at | Result |
| --- | --- |
| Archive extraction | absolute paths, `..`, drive letters and symlinks refused; entries count and size capped. **Fixed**: the size is now counted while unpacking, so a zip whose directory lies about sizes can't fill the disk. |
| Before code runs | manifest, labdex version range and signature are checked before any plugin code is imported; a plugin whose files changed after signing isn't loaded |
| Repositories | the key is pinned when added; index and plugins must be signed with it; downloads must match the index's SHA-256 |
| Official plugins | not part of the package (1.24.1+): installed from the official repository, signed with the project key, like any repository plugin |
| What a plugin can do | official and built-in plugins: anything labdex can. Community and unsigned plugins (1.5+) run in a process of their own with an empty environment, locked by Landlock to Python, their own folder, a data folder of their own and a private temporary folder; they get the plugin API over a pipe, with only their own instance's settings. Their Ansible playbooks run inside the same lock. labdex's own process is non-dumpable, so no plugin can read its memory. Unsigned plugins still need an administrator's explicit OK. |
| Demo mode | plugins can't make HTTP requests or run Ansible, only the demo plugins can have instances |

## Backups

| Looked at | Result |
| --- | --- |
| Encryption | scrypt-derived key, AES-256-GCM, authenticated |
| Restore | archive entries must be plain files at the top level, and every file must match the manifest's SHA-256. **Fixed**: the unpacked size is capped, so a gzip bomb can't exhaust memory. |
| Secrets | encrypted with `secret.key`, which is never in a backup |

## By design

- Administrators can make labdex connect to any address (integrations, notification
  channels, repositories). That is their job; don't give admin to people you wouldn't give
  network access.
- Login throttling is per process. With several gunicorn workers, each counts on its own.
- Anyone who can write to the data folder can add plugin code. Protect `/data`.
- An isolated plugin still reads the whole inventory (as the plugin API offers) and can
  reach the network: it can send what it reads anywhere. Isolation keeps secrets and
  labdex itself safe, not the inventory's contents.
- Without Landlock in the kernel (before Linux 5.13, or a container runtime that
  blocks it), `on` still runs isolated plugins in their own process, but they could
  read files labdex can. The Plugins page says which applies; `strict` refuses to run
  them then.
