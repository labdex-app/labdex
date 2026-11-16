# Pulumi (public site)

The **Pulumi** integration keeps the hosts of a site in existence at cloud providers: a
**Contabo** instance per node and a **Cloudflare** `A`/`AAAA` record per name. You describe the
nodes in labdex like any other host; labdex turns them into a Pulumi program, shows what
`pulumi preview` would change as a plan, and runs `pulumi up` once the plan is approved.

Add it under **Plugins › Pulumi (public site) › Add**, and put it in the site it should manage
(usually [`public`](../guide/sites.md)). The engine is part of labdex (`labdex.iac`); the plugin
itself lives in `plugins/pulumi/` of the labdex repository until it gets a repository of its own
and a place in the Marketplace.

## What it needs

- The Pulumi SDK and CLI in labdex: build the image with `docker build --build-arg PULUMI=1 .`,
  or for a checkout `pip install -e "backend[pulumi]"` plus the CLI from pulumi.com.
  *Test connection* says what's missing.
- A **state passphrase**. Pulumi keeps its state in labdex's data folder
  (`pulumi/<integration>/`), one stack per integration named after its site, with its secrets
  encrypted under this passphrase. No Pulumi Cloud account is involved. Backups carry the
  stacks as `pulumi-state.tar.gz`; to restore them, unpack that file into the data folder
  (`tar -xzf pulumi-state.tar.gz -C /data`).
- For Contabo: an API client (client ID and secret) and API user (e-mail and password) from the
  Customer Control Panel › API.
- For Cloudflare: an API token limited to *Zone › DNS › Edit* on one zone, the zone's name and
  its ID.

## What it makes

For every host the integration applies to (its site, its *Applies to* selectors and per-host
switches):

| Resource | From |
| --- | --- |
| Contabo instance | the host's name as display name; product, region and image from the host's settings or the integration's defaults; SSH keys on new instances |
| Cloudflare `A` record | `<record name>.<zone>`, with the template `{fun}` (the host's first fun name, else its name) or `{host}`; `{fun}.node` gives `everest.node.example.com`. The address is the host's primary public IPv4, or the new instance's address while the inventory has none |
| Cloudflare `AAAA` record | the same, for the primary public IPv6, when *AAAA records too* is on |

Private addresses (RFC 1918, CGNAT, ULA, link-local) never get a record. *Show what Pulumi would
keep* lists the resources without running Pulumi.

## Existing nodes and records

Nothing has to be recreated. On a host's page, the Pulumi panel takes:

- **Contabo instance id**: Pulumi adopts that instance (an import) instead of ordering a new
  one. The integration's *Contabo instances* page lists every instance with its id and the host
  it belongs to.
- **Existing A / AAAA record id**: the same for Cloudflare records.

## Safety

- Every change is a plan first, with the usual approvals, change windows and history.
- Instances are **protected** by default: a host taken out of the inventory never deletes its
  VPS. Turn *Protect instances* off to let a plan remove one, and *Cancel instances removed from
  the inventory* on to actually cancel it at Contabo; otherwise labdex only forgets it.
- Changing an instance's product, region or image means a new instance; the plan says
  *replaced* so it can't pass unnoticed.
- Credentials are passed to Pulumi as environment variables for the run; they're never written
  to the stack's state or shown in a plan.
- *Read back from the providers* (`pulumi refresh`) updates the state from Contabo and
  Cloudflare, so changes made by hand show up in the next plan.

## Limits of this first version

- Contabo instances are a Pulumi *dynamic resource* (there's no Contabo package for Pulumi on
  PyPI): create, rename, adopt and cancel. Reinstalling, snapshots and private networks aren't
  managed yet.
- Cloudflare R2 buckets, other record types and other providers (Hetzner, AWS …) would be new
  resource types in `labdex.iac`.
- New instances' addresses are logged after an apply; add them to the host yourself.
