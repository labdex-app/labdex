# DNS

labdex derives every record from the inventory:

- `A`/`AAAA` per interface: `<host>.<network>.<domain>`, or into the network's own zone.
- The host's short name, fun names and service names as CNAMEs in the domain (or the
  host's main zone).
- `SRV`/`TXT` per service when DNS-SD is ticked.
- Extra records per host (an alias that follows the primary record, A/AAAA, TXT, SRV,
  MX) and global custom records with `{ip4}`/`{fqdn}` placeholders.

Download them as a zone file or JSON (File exports), or let the Technitium plugin keep
a server in step: it syncs every zone labdex uses, puts each record into the closest
zone on the server, and only touches records it created.

## Split horizon

A public service's name (`dns.example.com`) is served by your public DNS with your
WAN address. With split horizon on, Technitium gets a zone of that name holding the
service's internal addresses, so inside the house the name resolves straight to the
host. labdex creates the zone as part of the plan and leaves public DNS alone.
