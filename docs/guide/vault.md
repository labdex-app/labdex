# The vault

Root passwords, IPMI logins, Wi-Fi keys, recovery codes: the vault keeps them in labdex,
next to the hosts they belong to, encrypted with a passphrase only you know.

## Setting it up

Inventory › **Vault** › *Set up the vault* (administrators). Choose a passphrase of at
least 12 characters; a few words are easy to remember and hard to guess.

**There is no way back from a lost passphrase.** Neither labdex nor a backup can open the
secrets without it. Write it down and keep it somewhere safe, apart from the backups.

## Sealed and unsealed

After every start of labdex the vault is **sealed**: names, paths and field names show,
values don't. An administrator opens it on the Vault page with the passphrase; it stays
unsealed until labdex stops or someone presses *Seal*. Five wrong passphrases in a minute
and labdex waits a minute before it tries again; every attempt is in History.

While sealed, path, host and description of an entry can still change, values can't.

## Entries

An entry has a **path** (`hosts/srv-pm-pve-01/root`, `wifi/iot`: lowercase, up to 8
parts), an optional **host**, a description and its **fields** (`username`, `password`,
`url` … up to 40). The ↻ button next to a value makes a 24-character password in your
browser.

- An entry of a host shows on the host's **Secrets** tab, and the host's rights apply to it
  (its zone, network, tags, name). Renaming the host keeps the link, and so does a YAML
  import.
- Every change makes a new **version**; the last 10 are kept, and each can be revealed.
- Values never appear in lists, exports, History or the API answers that list entries.
  The YAML export doesn't contain the vault; back it up with labdex's own backups.

## Revealing

The eye shows one field, for 30 seconds; the copy button copies it without showing it.
Both need `secret:apply` on the entry, and both are written to History (*revealed
hosts/srv-pm-pve-01/root#password*). In the browser, labdex asks for your password again
before the first reveal, and again after 5 minutes.

## Rights

| Permission | Lets you |
|---|---|
| `secret:view` | see paths, field names, versions (the *viewer* role has it) |
| `secret:apply` | reveal and copy values |
| `secret:edit` | add, change and delete entries |

`secret:apply` and `secret:edit` are only ever given by name: a role with `*` (or
`secret:*`) gets `view` only. The built-in **secret-keeper** role has both; bind it on a
scope like `zone:DC_*` or `name:wifi/*` (path scopes work for entries without a host).
Administrators may do everything and alone set up, unseal, seal and change the passphrase.

## Pointing at secrets

Plugins and Ansible variables don't hold a copy, they point at a field:

- a plugin's secret setting: *from the vault*, then pick `fw/opnsense#key`;
- an Ansible variable: `api_key: {$secret: vault:fw/opnsense#key}`.

The value is read when the job runs, so a changed secret reaches every user on its next run.
Pointing at a secret for the first time needs `secret:apply` on it, because it hands the
secret to the plugin or to Ansible; what someone else pointed at stays when you edit. An
entry's dialog shows who uses it; it can't be deleted while anything does, and renaming
it takes the references along.

While the vault is sealed, jobs that need a secret fail and say so. They run in the labdex
process (the default); the separate `labdex worker` process can't read the vault.

**Before 1.18** shared secrets lived under Settings › Secrets, encrypted with `secret.key`.
They move into the vault as `global/<name>` (field `value`) the first time an
administrator sets it up or unseals it, and `{$secret: name}` keeps meaning
`vault:global/<name>#value`, so nothing that points at them breaks. `/api/secrets` still
works on those `global/` entries.

## How it's built

```
passphrase ──scrypt──▶ key-encryption key ──AES-GCM──▶ master key (random, 32 bytes)
master key ──AES-GCM──▶ a data key per version ──AES-GCM──▶ the values (JSON)
```

- Only the wrapped master key is stored. The unwrapped one lives in the memory of the
  labdex process, never on disk.
- Each ciphertext is bound to its entry and version, so blobs swapped between entries
  don't decrypt.
- Changing the passphrase re-wraps the master key; the entries stay as they are.
- `secret.key` (for integration settings) has nothing to do with the vault: neither
  opens the other.
- labdex runs as one process (the Docker image starts one worker with threads). If you
  run several workers or processes, each one would have to be unsealed on its own; the
  separate `labdex worker` process never reads the vault.
