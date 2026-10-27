# OPNsense

Checked against OPNsense 26.1. Give labdex its own API user with access to the pages
for the actions you use: aliases, automation filter rules, destination NAT, Kea DHCP,
interfaces and diagnostics (ARP).

| Action | Mode | |
| --- | --- | --- |
| Check connection | run | |
| Sync aliases | plan | generated and custom aliases, category `automation:labdex`; adds hosts to aliases you made by hand and only removes what it added |
| Sync policy rules | plan | rules from the [zone matrix](../guide/policy.md) and services' *allow from* |
| Sync port forwards | plan | destination NAT for public services |
| Sync DHCP reservations | plan | Kea DHCPv4 reservations from MAC/IP pairs |
| Check access | run | every service's *allow from* against the pass rules |
| Import ARP / leases | run | into Discovery |

Pages: *Firewall access* (the access check for every host). How the firewall matches the
zone matrix (pending rule changes, rules no policy explains, zones managed by labdex) is on
Network › Policy › *On the firewalls*. On a host: the rules
that mention it and its access check.

Settings worth knowing: `wan_interface` and `nat_destination` for port forwards.

Everything labdex writes carries the category `automation:labdex` and a key in the
description; it never changes anything else.
