# CLI

```sh
labdex import inventory.yaml       # replace the inventory (asks first; --yes to skip)
labdex export -o inventory.yaml    # write the inventory
labdex lint                        # exit 1 on errors; --warnings-as-errors for strict CI
labdex diff --since 7d             # what changed since then (24h, 2w or a date; --fields for before/after)
labdex user create NAME [--admin]  # labdex user password NAME
labdex token create USER NAME [--read-only] [--days 90]
labdex plugins                     # plugins and integrations
labdex action fw-main sync_aliases [--apply]
labdex worker [--slots N]          # run jobs (LABDEX_WORKER=external)
labdex backup create -o FILE | check FILE | restore FILE [--inventory-only]
labdex db current | upgrade | copy --to URL
labdex plugin keygen | sign | pack | repo
```

Every command reads the same environment as the server (`LABDEX_DATA`, `LABDEX_DB`).
