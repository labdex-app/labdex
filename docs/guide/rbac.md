# Users, roles, history

- **Sign-in**: local passwords (10+ characters, throttled after 5 failures), SSO headers
  from a trusted proxy, or API tokens (`Authorization: Bearer ldx_…`, optionally
  read-only and expiring, stored hashed).
- **Permissions** are `<type>:<action>`: types `host`, `network`, `alias`,
  `integration`, `discovery`, `audit`, `secret`; actions `view`, `edit`, `apply`, `admin`.
  Built-in roles: viewer, editor, operator, integration-admin, secret-keeper, admin.
  For secrets a `*` (all actions) covers only `view`: revealing and changing them is
  always given by name (`secret:apply`, `secret:edit`). See [the vault](vault.md).
- **Role bindings** give a user or group a role on a scope: `*`, `zone:DC_*`, `area:CA`,
  `network:srv`, `tag:admin`, `name:srv-vm-*`. An editor on `zone:CA_*` can't see or
  touch the datacenter.
- **Object grants** add exceptions for single objects, e.g. edit on
  `host:srv-vm-media-*` or apply on `integration:motd`.
- Moving an object needs edit rights on its old and its new place.
- **History** records every create, update, delete, import, approval, apply and sign-in
  with a field diff. Hosts, networks and aliases can be reverted; a revert is a change
  of its own.
