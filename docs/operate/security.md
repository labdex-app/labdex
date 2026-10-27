# Security

## Deploying

- Put labdex behind a reverse proxy over HTTPS and set `LABDEX_COOKIE_SECURE=true`.
  SSO headers are only trusted from `LABDEX_TRUSTED_PROXIES`.
- The container runs as an unprivileged user with a read-only root filesystem; only
  `/data` is writable.
- Give every integration its own least-privilege API user or token.
- Back up `/data` and keep `secret.key` apart from the backups.

## What labdex does

- Passwords are hashed with scrypt; sign-in is throttled after 5 failures per user
  and client address (the real client behind a trusted proxy) and 20 per user overall. API tokens are stored hashed and can be read-only and expiring.
- Session cookies are HttpOnly and SameSite=Strict; every write needs the header
  `X-Requested-With: labdex`, so other sites can't post to labdex. A strict
  Content-Security-Policy, no framing.
- Secrets are encrypted at rest (Fernet: AES with HMAC) and never returned by the API.
  They are masked in job logs.
- The [vault](../guide/vault.md) is sealed after every start: its master key is
  wrapped with a passphrase that labdex never stores (scrypt, AES-256-GCM, a key per
  version), so a backup or a copy of the database alone opens nothing. Every reveal is
  in History.
- Every change is in History with who, when and what.
- Plugins are checked (manifest, version range, signature) before any of their code
  runs; a plugin whose files no longer match its signature isn't loaded.
- Backups are encrypted with your passphrase (scrypt, AES-256-GCM) and checked before
  a restore.

See the [security review](security-review.md) for what was checked before 1.0.

## Verifying a release

Release images and archives are signed with cosign, keyless, from the release
workflow, and come with an SPDX SBOM:

```sh
cosign verify ghcr.io/OWNER/labdex:1 \
  --certificate-identity-regexp 'https://github.com/OWNER/labdex/.github/workflows/release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-blob labdex-1.0.0.tar.gz --bundle labdex-1.0.0.tar.gz.bundle \
  --certificate-identity-regexp 'https://github.com/OWNER/labdex/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Reporting a problem

Please report security problems privately through GitHub's *Report a vulnerability*
on the repository, not in a public issue.
