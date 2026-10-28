# Upgrading

!!! warning "1.24.1: the image has a new name"
    labdex moved to the GitHub organization **labdex-app**. From 1.24.1 on, the image is
    `ghcr.io/labdex-app/labdex`; `ghcr.io/reykimedev/labdex` stays at 1.24.0 and gets no more
    updates. Change the image in your compose file (or `docker run`) once, then upgrade as
    below. Your data, settings and plugins stay as they are.

    ```yaml
    services:
      labdex:
        image: ghcr.io/labdex-app/labdex:1
    ```

Replace the image or package and start labdex: the database schema is migrated on
start, from any release since 0.4. There are no manual steps, and the CI checks this
by upgrading a 0.5 database to the current release.

```sh
docker compose pull && docker compose up -d
labdex db current       # the schema revision, if you're curious
```

With the release check on (Settings › General), administrators see a bar at the top of
every page when a newer release is out, with its notes and this command to copy. labdex
doesn't upgrade itself: that would mean giving the container control of Docker. If you
want it hands-off, a tool like Watchtower can pull new images for you.

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
