# Zones and names

## Zones

- An **area** says where something is: `INET` internet, `CA` campus, `RA` remote
  access, `DC` datacenter, `DMZ` perimeter. Areas are yours to define, each with a
  colour.
- Every area has a **default zone** with the area's own code (`DC`): for networks,
  hosts and interfaces you haven't classified further. It shows as just the area.
- Further **zones** are made one by one inside an area, `<AREA>_<NAME>`: `DC_TC` for
  trusted servers, `DC_HT` for management, `CA_UT` for IoT and guests. Each has a name,
  a **rank** (higher = more trusted) and optionally its own colour.
- Codes are typed as names: `Trusted clients` becomes `DC_TRUSTED_CLIENTS` (capitals,
  spaces and dashes turn into `_`). An area's code is up to 16 letters or digits without
  `_`, since `_` separates the area from the zone; a whole zone code is up to 48
  characters. Short codes are still handy: they appear in prompts and firewall aliases
  (long alias names get a shortened firewall id).

Zones live on the interface: every address takes its network's zone. A host's *home
zone* follows its primary interface unless you pin it; it decides RBAC scopes, the
prompt colour and the placement check. A firewall sits in every zone it has an
interface in; give it the zone of its management interface as home zone.

Lint enforces **placement**: a device never sits in a network ranked above its own
zone. A `CA_UT` IoT device in a `DC_TC` server network is a warning. Zones without a
rank (the area defaults) aren't checked.

## Hostnames

Functional names are `<category>-<type>-<role>-<nn>`, e.g. `srv-vm-dns-01`:
categories and types come from the lists under Naming, the role is free text, and
the number is the next free one. They say what a host does, which is what you want
in a prompt, a firewall rule or a log line.

**Fun names** are what you remember (`arcanine`, `resolver`). A host can have several;
each becomes a DNS CNAME and the first shows in the login banner and prompt. Pools of
fun names ship with labdex; paste your own and tie them to a category or zone, and
labdex suggests unused ones.

## Firewall aliases

labdex generates aliases per network, zone, area and host, and you add your own groups
whose members reference hosts, networks, zones or other aliases:

| Alias | Holds |
| --- | --- |
| `N4_ZONE_DC_TC_SRV` | the IPv4 prefix of network `srv` |
| `NG_ZONE_DC_TC` | every network in zone `DC_TC` |
| `NG_ZONE_DC` | every network in area `DC` |
| `NG_ZONE_DC_DEFAULT` | the networks in `DC`'s default zone |
| `H_ZONE_DC_TC_SRV_VM_DNS_01` | the addresses of one host |
| `HG_…`, `MG_…`, `SG_…` | your host, MAC and port groups |

On the firewall each alias gets a short uid (`ldx_3fa9c201`, stable for its name) or,
if you prefer under Settings → Conventions, a readable id (the name if it fits, else a
shortened form); you can also set one per custom alias. The full name goes into the
description. The OPNsense plugin can import the aliases you already have on the
firewall.
