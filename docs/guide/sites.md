# Sites

A **site** is a part of your infrastructure with its own hosts, networks, integrations and
credentials. A typical setup has two: `home`, behind your own firewall, and `public`, the
machines and DNS records at a cloud provider. One labdex shows both, so a question like
"which Debian hosts still need patching?" has one answer across all of them.

Every inventory has at least one site. After an upgrade everything you had is in `home`, and
with only one site nothing in the UI changes: the site fields and filters appear once you add a
second one.

## What belongs to a site, and what is shared

| Per site | Shared by every site |
| --- | --- |
| Hosts and networks | Areas, zones and the zone policy |
| Integrations, their settings and credentials | Naming rules and fun name pools |
| The DNS records, firewall aliases, Ansible inventory and DHCP reservations generated from them | Features, tags, racks |
| | Custom aliases, users, roles, notification channels |

A host and its networks are always in the same site, and so are a VM and the hypervisor it runs
on. VLAN numbers are unique across the whole inventory for now.

## Adding a site

**Settings › Sites › Site**: a short code (`public`; it's used in selectors and file names), a
name, a colour and a description. The first site in the list is where new hosts and networks go
unless you pick another one; the arrows reorder the list.

With two or more sites:

- the host and network forms have a **Site** field, and a host only offers the networks of its site;
- the **Hosts** page has a *Site* filter, `site:public` in the search box and *Group by › site*;
- the **Networks** page has a site picker;
- read endpoints take `?site=`: `GET /api/dns?site=public` lists only the public records,
  `GET /api/aliases?site=home` only the home aliases.

A site can only be deleted when it's empty, and the last site can't be deleted at all.

## Integrations work in one site

Every integration has a **Site** (on its page, under *Settings*). It only sees that site: its
hosts, networks, DNS records, aliases and Ansible inventory are built from that site alone. The
home OPNsense never gets a public node's alias and a public DNS provider never gets your home
records.

Backups (and the demo) work for **every site**. A plugin can ask for that in its manifest with
`site: all`; any other new integration starts in the first site.

Plugins can read their site as `self.ctx.site`; `self.ctx.inventory.sites()` lists all of them.

## Reaching across sites

A site may *reference* another site's objects read-only. A custom alias that lists
`host:srv-vm-oak-01` or `network:contabo` from the public site resolves, for the home firewall,
to that host's addresses or that network's prefixes. That's how a home firewall rule can allow
traffic from the public nodes without the home site owning them.

## Roles

Role scopes and integration selectors understand `site:`: a role bound with `site:public` sees and
edits only public hosts and networks, and `site:home tag:k3s` selects home k3s nodes.

## One file per site in git

Each site's networks and hosts can be kept as a file of its own in a git repository, for example
`labdex/home.yaml` and `labdex/public.yaml`:

```sh
labdex sites                                         # the sites, with their counts
labdex export --site public -o labdex/public.yaml    # that site's networks and hosts
labdex import labdex/public.yaml                     # replaces only the public site
```

A site file starts with `site: public` and holds only `networks:` and `hosts:`. Importing it
replaces that site's networks and hosts and leaves every other site, and everything shared,
alone; a site that doesn't exist yet is created. The whole-inventory export (`labdex export`,
Settings › Backup) lists the sites under `sites:` and gives each host and network outside the
first site a `site:` key.
