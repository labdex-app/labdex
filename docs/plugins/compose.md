# Compose stacks

The **Compose stacks** integration keeps your Docker Compose stacks in labdex and writes them to
the Docker hosts with Ansible. Like other plans in labdex, nothing reaches a host before a person
approved the run, and labdex only ever changes folders it wrote itself.

Add it under **Plugins › Compose stacks › Add**. It lives in `plugins/compose/` of the labdex
repository for now.

## What it needs

- Ansible in labdex: build the image with `docker build --build-arg ANSIBLE=1 .`. *Check setup*
  says what's missing.
- SSH to the Docker hosts: an SSH user and private key, and sudo for that user (the stack folder
  is created as root, `docker compose` runs as root).
- Docker with the Compose plugin (`docker compose`) and Python 3 on the hosts.

## Stacks

**Workloads** (in the sidebar under Operate) lists every stack of every Compose integration you
may see, with its host, services and last deploy. Pick one to read or change its compose file:
*Diff* shows the change before *Save*, which changes labdex only (recorded in Activity as an
update of the integration; comments in the *Stacks* text are not kept). *Plan deploy* plans that
one stack, and the plan waits on Changes for an approval like any other.

*Stacks* is a YAML list. Each stack names the host it runs on, its compose file and, if it needs
one, its environment:

```yaml
- name: grafana
  host: srv-vm-docker-01
  compose:
    services:
      grafana:
        image: grafana/grafana:11.3.0
        ports: ["3000:3000"]
        environment:
          GF_SECURITY_ADMIN_USER: ${GF_ADMIN_USER}
          GF_SECURITY_ADMIN_PASSWORD: ${GF_ADMIN_PASSWORD}
  env:
    GF_ADMIN_USER: admin
    GF_ADMIN_PASSWORD: {$secret: vault:apps/grafana#admin}
```

- `name` is the compose project name and the folder: lower-case letters, digits, `-` and `_`.
- `host` must be a host of the inventory that this integration applies to.
- `compose` is the compose file itself, with at least one service.
- `env` becomes the stack's `.env` file. A value is either written as is or is a reference to the
  [vault](../guide/vault.md), `{$secret: vault:path#field}`. Values are quoted so that compose
  takes them literally (a `$` in a password stays a `$`).

On the host a stack becomes `<folder for stacks>/<name>/` (`/opt/stacks` by default) with
`compose.yaml`, `.env` (mode 0600) and `.labdex`, a marker with fingerprints of what labdex wrote.
*Show compose files* prints the files without connecting to any host.

## Deploy stacks

*Deploy stacks* is a plan:

1. **Plan.** labdex reads the stack folders on the hosts (read-only) and compares each compose
   file with the one in labdex, and the env by the fingerprint of its references. Secret values
   never leave the vault for a plan. The plan shows a diff of every compose file and says when
   the `.env` changed. A folder of the same name that labdex didn't write (no `.labdex` marker)
   stops the plan: rename the stack or move the folder.
2. **Approve.** Every run waits for a person's approval, even when an
   [approval policy](../guide/plans.md) would let it apply by itself.
3. **Apply.** One play over the planned hosts: it writes the files, fills in the secrets for this
   run only (the `.env` task never shows up in the log), pulls the images if *Pull images before
   starting* is on, and runs `docker compose up -d --remove-orphans`.

| Parameter | |
| --- | --- |
| Only these stacks | stack names; empty = every stack |

A stack you take out of labdex keeps running on its host. With *Remove stacks taken out of
labdex* on, the plan includes it as a removal instead: `docker compose down`, then its folder is
deleted. Only folders with the `.labdex` marker are ever removed, and volumes are kept.

!!! note "Hosts in public sites"
    Approval policies decide who may approve which plan. Give the hosts of a public site a policy
    with the approvers you trust there; the plugin itself never deploys without an approval.
