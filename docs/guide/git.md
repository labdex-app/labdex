# Inventory in git

The inventory can live in a git repository as YAML, so a change is a commit and a merge request
shows what it would do before anyone merges it. This page covers the first part: **planning a file**,
with `labdex plan`, in a terminal or in CI.

## The files

Keep the whole inventory in one file, and add one file per [site](sites.md) when sites are edited
on their own:

```text
labdex/
  inventory.yaml   # everything: zones, naming, aliases, features, every site (labdex export)
  home.yaml        # the home site's networks and hosts (labdex export --site home)
  public.yaml      # the public site's
```

A site's file replaces that site's part of `inventory.yaml` when it's imported after it.

`labdex export --site public -o labdex/public.yaml` writes a site's file, and `labdex import labdex/public.yaml`
replaces only that site.

## labdex plan

`labdex plan FILE` says what importing the file would change, and keeps nothing:

```console
$ labdex plan labdex/home.yaml
Plan for site home: 0 to add, 1 to change, 1 to remove.
  ~ host srv-vm-dns-01
      interfaces[0].ip4: 10.10.100.53 → 10.10.100.199
  - host srv-vm-mon-01
DNS records:
  + srv-vm-dns-01.srv.lab.example.net A 10.10.100.199
  - srv-vm-dns-01.srv.lab.example.net A 10.10.100.53
  …
Firewall aliases:
  ~ H4_ZONE_DC_TC_SRV_VM_DNS_01: +10.10.100.199, -10.10.100.53
  - H_ZONE_DC_TC_SRV_VM_MON_01
```

It lists the inventory objects that change, field by field, and what labdex would generate differently:
DNS records, firewall aliases and DHCP reservations. For a site's file, only that site's output counts.

| Option | |
| --- | --- |
| `--base FILE` | Import this first, instead of comparing with this labdex's database. Repeat it for several files, in order. |
| `--base-ref REF` | Take the base from git: the same file at `REF` (`origin/main`). A file that's new there has none. |
| `--site CODE` | The file is this site's (default: its `site:` key). |
| `--format` | `text` (default), `json`, or `markdown` for a merge request comment. |
| `--detailed-exitcode` | Exit 2 when there are changes. 0: no changes, 1: the file has errors. |

Admins can do the same over the API: `POST /api/admin/plan` with `{"yaml": "…", "base": ["…"], "site": "home"}`
returns the plan as JSON, with a `markdown` rendering and an `empty` flag.

## In CI

CI needs no labdex server: point `LABDEX_DB` at a throwaway SQLite file, import the target branch's files
as the base, and plan the merge request's file on top. Every change comes from git, nothing from a database.

A GitLab CI job that comments the plan on the merge request (the token is a project access token with the
`api` scope, in a masked variable `LABDEX_PLAN_TOKEN`):

```yaml
labdex-plan:
  image: ghcr.io/labdex-app/labdex:1  # a release with labdex plan
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      changes: [labdex/*.yaml]
  variables:
    LABDEX_DATA_DIR: $CI_PROJECT_DIR/.labdex
    LABDEX_DB: sqlite:///$CI_PROJECT_DIR/.labdex/plan.db
  script:
    - git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - git show "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME:labdex/inventory.yaml" > /tmp/base.yaml
    - |
      for f in labdex/home.yaml labdex/public.yaml; do
        labdex plan "$f" --base /tmp/base.yaml --base-ref "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --format markdown >> plan.md || status=1
      done
    - >
      python -c "import json,sys; print(json.dumps({'body': open('plan.md').read()}))" |
      curl -sS --fail -X POST -H "PRIVATE-TOKEN: $LABDEX_PLAN_TOKEN" -H "Content-Type: application/json" --data @-
      "$CI_API_V4_URL/projects/$CI_PROJECT_ID/merge_requests/$CI_MERGE_REQUEST_IID/notes"
    - exit ${status:-0}
```

`labdex lint` runs the same way, to fail the pipeline on convention errors.

## What comes next

Labdex reading the repository by itself (on a push or a webhook), and UI edits becoming commits or merge
requests, build on this. Until then, the [YAML mirror](../plugins/index.md) plugin writes the inventory to a
git working copy after every change.
