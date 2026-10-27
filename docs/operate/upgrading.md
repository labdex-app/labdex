# Upgrading

Replace the image or package and start labdex: the database schema is migrated on
start, from any release since 0.4. There are no manual steps, and the CI checks this
by upgrading a 0.5 database to the current release.

```sh
docker compose pull && docker compose up -d
labdex db current       # the schema revision, if you're curious
```

Take a backup first (`labdex backup create -o FILE`) if you want a way back: a
migrated database can't be opened by the older release.

## Within 1.x

From 1.0 on, labdex keeps within a major version:

- the plugin API (see the [reference](../develop/plugin-api.md)): manifest keys,
  methods and their arguments only grow;
- the REST API under `/api`: fields and endpoints are added, not removed or renamed;
- the YAML format: older files import;
- the backup format: older backups restore.

Anything else that has to go is deprecated in a minor release, noted in the changelog,
and removed in the next major one.

Plugins say which versions they work with (`labdex: ">=1.0,<2"`); one outside its
range after an upgrade isn't loaded, and its page says why.
