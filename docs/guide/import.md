# Coming from another tool

labdex can add networks and hosts from **NetBox**, **phpIPAM** or **CSV files** (a spreadsheet
exported as CSV is the usual way in). Under **Settings › Add from other tools**, or on the
command line with `labdex import-from`.

It only ever **adds**. A network or host that exists already (by name; networks also by prefix)
is left exactly as it is and listed as skipped, so you can run it again after fixing the source.
Every new object is a History entry (*imported*), so each can be reverted. Always run the
**preview** first: it does everything except keep the result, and lists what would be added, what
would be skipped and what doesn't pass labdex's checks.

## Zones

The other tools don't have zones, so everything comes in under the zone you choose, and a
network can get another one: `zone` in a CSV, or `--zone-map srv=DC_TC` on the command line. A
host's zone follows its primary interface's network, as usual. Afterwards the Hosts page
(filter by zone) and Lint (placement) show what's left to sort out. If you have no zones yet,
create them first under Settings › Zones; the example set is one click.

## What comes across

| From | Networks | Hosts |
| --- | --- | --- |
| **NetBox** | IPv4 prefixes: VLAN name and ID, description | Devices and virtual machines: role as *function*, manufacturer and model, status, tags, comments; interfaces from their IP addresses, with MAC addresses; the primary IPv4 is the primary interface |
| **phpIPAM** | IPv4 subnets (folders skipped): VLAN name and number, description, gateway | Addresses that have a hostname: description as *function*, MAC address |
| **CSV** | `name`, `prefix`, `vlan`, `gateway`, `zone`, `notes` | `name`, `ip`, `mac`, `network`, `zone`, `function`, `status`, `tags`, `vendor`, `model`, `notes`; a host on several rows gets several interfaces |

Names are made safe: `Core-Switch.lab.example` becomes the host `core-switch` (the original goes into
its notes for NetBox). IPv6 addresses and prefixes aren't brought in yet; the preview counts them.
An address that falls in no network labdex has, or that another host already uses, is skipped with
a note rather than failing the host. A network whose VLAN number is taken comes in without the VLAN.
Host names that don't follow your naming rules come in anyway (Lint shows them) so you can rename
them in place.

## On the command line

```sh
# CSV: hosts and/or networks, preview first
labdex import-from csv --zone DC_TC --networks nets.csv --hosts hosts.csv
labdex import-from csv --zone DC_TC --networks nets.csv --hosts hosts.csv --apply

# NetBox (the token is read from the environment, not the command line)
LABDEX_IMPORT_TOKEN=… labdex import-from netbox --url https://netbox.example.net --zone DC_TC --zone-map mgmt=DC_HT

# phpIPAM (the password from LABDEX_IMPORT_PASSWORD, or asked for)
labdex import-from phpipam --url https://ipam.example.net --app-id labdex --username admin --zone DC_TC
```

`--no-verify-tls` skips the certificate check for a self-signed server. The NetBox token needs
read access only; labdex reads prefixes, devices, virtual machines, IP addresses and interfaces
and writes nothing back.

!!! note
    The NetBox and phpIPAM readers follow those tools' documented REST APIs (NetBox 4.x,
    phpIPAM 1.5+) and are tested against sample answers, not live servers. If an answer from
    yours isn't understood, [open an issue](https://github.com/labdex-app/labdex/issues) with
    the (anonymised) output of the preview.
