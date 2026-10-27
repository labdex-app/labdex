# Backups

The `backup` module exists from the first start and backs up daily to
`/data/backups`. Each backup holds a database snapshot and the inventory YAML as
`.tar.gz`, encrypted with your passphrase (scrypt and AES-256-GCM). Targets: a folder
(local or NFS) or S3 (AWS, MinIO, Garage); S3 needs a passphrase. Retention is a
number of backups.

Keep `secret.key` somewhere else (password manager, offline): without it the secrets
in a backup can't be decrypted, and a backup without its passphrase is useless.

```sh
labdex backup check labdex-backup-20261023-020000.tar.gz.enc
labdex backup restore FILE --inventory-only       # just the inventory, the server may run
labdex backup restore FILE                        # the whole database: stop the server first
```

Every backup carries a portable `database.json`, so it restores into SQLite or
PostgreSQL alike.
