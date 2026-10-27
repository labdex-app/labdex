# Plugins

A new labdex installs four plugins: Backup, File exports, Ansible runner and nmap. The
others ship with labdex too and wait in **Plugins › Marketplace**: installing one takes a
click and downloads nothing. Uninstalling one is refused while it has instances; it
stays part of labdex and can be installed again any time. Upgrading from a version before
1.21 keeps every plugin you have an instance of.

The Marketplace also lists what your [repositories](installing.md#repositories) offer,
with a filter for updates. Anyone who can see integrations can browse it; administrators
install.

Under **Plugins** add an instance of a plugin (an integration), fill in its settings and
enable it. A plugin can have several instances, e.g. one per firewall. Each plugin has a
page listing its instances, what it can do and its signature.

| Plugin | What it does |
| --- | --- |
| [OPNsense](opnsense.md) | Aliases, rules from the zone matrix, port forwards, Kea reservations, access check, ARP and leases into Discovery |
| [Technitium DNS](technitium.md) | Records and split-horizon zones, plan/apply; stale and unmanaged records per host |
| [Ansible runner](ansible.md) | Features as playbooks and roles, `--check --diff` as the plan; host actions |
| Login banner & prompt | Banners and a zone-coloured prompt over Ansible |
| nmap | TCP connect scans of inventory networks into Discovery; every declared service port is checked too, whatever the port list |
| [VictoriaMetrics / Prometheus](monitoring.md) | Host status from `up`, CPU and memory widgets |
| [Beszel](monitoring.md#beszel) | Host status and resources; adds systems, installs agents |
| [Uptime Kuma](monitoring.md#uptime-kuma) | A monitor per service; status read back |
| [Certificates](monitoring.md#certificates) | TLS expiry of every https service |
| [Proxmox VE](compute.md) | Imports guests as hosts, links, drift |
| [k3s / Kubernetes](compute.md#k3s) | Nodes, readiness, versions, drift |
| [Tailscale / Headscale](compute.md#tailscale) | Tailnet devices into Discovery, online state |
| [UniFi Network](compute.md#unifi) | Clients into Discovery, devices as host status, cables from LLDP |
| [SNMP neighbours](compute.md#snmp-neighbours) | Cables from LLDP and CDP over SNMP v2c/v3 |
| [Traefik](compute.md#traefik) | HTTP routers as services with their host names |
| [Docker](compute.md#docker) | Containers as hosts, published ports and Traefik labels as services, state as status |
| [Routing discovery](compute.md#routing) | BGP neighbours and routes vs. the inventory (FRR, IOS-XE) |
| Prometheus service discovery | `http_sd_configs` endpoints |
| Backup | Daily encrypted backups to a folder or S3 ([Backups](../operate/backups.md)) |
| YAML mirror | The inventory as a file after every change, optionally in git |
| File exports | OPNsense alias import, DNS zone, Ansible inventory, DHCP CSV, YAML |

## Applies to

Integrations that work per host have selectors in the same syntax as role scopes. On
a host's page each one shows whether it applies (and why), can be forced on or off,
holds the host's own settings and actions, and shows a panel the plugin fills.

## Secrets

API keys and tokens typed into a plugin's settings are encrypted at rest with a key
derived from `secret.key` and are never sent back by the API. Shared ones live in the
[vault](../guide/vault.md): pick *from the vault* on a secret setting and choose a field
(`vault:fw/opnsense#key`); rotating it there updates every user. The list holds what you
may reveal, since pointing at a secret hands it to the plugin.

## Widgets and status

Plugins can show widgets on the dashboard and host pages, and report host and service
status. A host is down if any monitoring source says so, up if one says up; without
monitoring, Discovery's last scan decides.
