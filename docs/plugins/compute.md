# Compute and network

## Proxmox VE

A read-only API token (PVEAuditor) is enough for importing and drift. **Import guests**
plans VMs and containers as hosts (names that don't follow your convention become
`srv-vm-<name>-NN` with the guest name kept as fun name), links existing hosts, and
reports drift (addresses, MACs, status) between the inventory and the cluster.

**Check drift** also hands the guests the inventory doesn't have to Discovery (turn
**Guests labdex doesn't know go into Discovery** off to stop it): their addresses show up under unknown hosts
as `Proxmox VM 104 on pve1`, with the guest name as host name and its MAC when it has one
network card. Running guests count as seen; *Add it as a host* starts from there.

### Creating guests

Off until you turn on **Create guests** in the instance; the token then needs the
PVEVMAdmin role plus `Datastore.AllocateSpace` on the storage and `SDN.Use` on the
bridges. Then the inventory is where a guest starts:

1. Add a host with status **planned**, an interface with its address in a network, and
   **Runs on** set to a Proxmox node (or set a default node in the instance).
   The type comes from the name (`srv-ct-…` is a container, `srv-vm-…` a VM) or from the
   host's Proxmox settings, where cores, memory, disk, storage and the template can be
   set per host too.
2. Run **Create planned guests** (or **Create this guest** on the host). The plan shows
   each guest: node, template, size, bridge, VLAN tag and addresses. Nothing in Proxmox
   changes until the plan is approved.
3. On apply, a container is made from the LXC template, a VM is cloned from the
   cloud-init template; the network comes from the inventory: the network's VLAN as the
   tag on the bridge (or the bridge per network you set), the static address with the
   network's gateway, IPv6 from the inventory or SLAAC, your SSH keys, DNS server and
   search domain. A MAC the inventory doesn't have yet is generated (Proxmox's
   `BC:24:11` prefix) and written back, so DHCP reservations and drift line up.
4. The host is linked to its guest and becomes **active** once started. If a later
   step fails (the VM doesn't start, say), the guest is still linked and the host set
   **offline**, so the next plan doesn't make a second one; the job log says what to fix.

Guests are never deleted or changed after creation; drift reports differences.

## k3s

A service account token with get/list on nodes and pods. Matches nodes to hosts,
reports Ready/NotReady as host status, lists drift against the cluster's feature and
mixed kubelet versions.

## Tailscale

Tailscale or Headscale. Tailnet devices go into Discovery; devices that are hosts
report online as status. Widgets for the tailnet and a host's device.

## UniFi

Clients go into Discovery with address, MAC and name; UniFi devices (switches, access
points, gateways) that are hosts report online or offline. Read-only. **Sign in with**:

| Setup | Choose | You need |
| --- | --- | --- |
| UniFi OS Server (self-hosted), or a console (UDM, UCG, Cloud Key) | Local account — UniFi OS Server or console | the URL (`https://host:11443` for UniFi OS Server, port 443 on a console), a local account |
| The Network application installed on its own | Local account — Network application | the URL with port 8443, a local account |
| A console with Network 9.3 or newer | API key | Network → Settings → Control Plane → Integrations |
| Anything adopted in unifi.ui.com | Site Manager API key | unifi.ui.com → API → Create API key |

The form only shows what the chosen sign-in needs.

**The account.** Make one for labdex: Admins & Users → add an admin, *Restrict to local
access only*, role *View Only* for Network. Accounts with two-factor sign-in (UI.com
accounts usually) can't be used; labdex says so. labdex signs out after each run.

**Site Manager.** labdex lists the consoles the key sees (or the ones named under
*Consoles*) and reads each through the UniFi cloud connector, which needs a recent
console firmware. A console the connector can't reach still reports its devices from
Site Manager, without clients; the job log says which. The certificate of api.ui.com is
always checked.

**Cables.** Signed in with a local account, *Read devices* also reads which port everything
sits on: each switch's LLDP neighbours, each device's uplink, and the switch port of every
wired client. They show up as suggestions under Inventory › Cables › *Seen by switches*. A
wired client has no port name of its own; its interface with that MAC is used (and named
`eth0` if it has no name yet). The API key sign-ins don't tell ports.

**TLS.** UniFi uses a self-signed certificate. Either leave *Check the TLS certificate*
off, or install a real certificate (or set a CA file) and turn it on.

## SNMP neighbours

Reads switches and routers over SNMP, v2c (a community) or v3 (a user with SHA, SHA-256,
SHA-512 or MD5 authentication and AES-128 privacy): who sits on which port, from LLDP and,
on Cisco gear, CDP. What it finds becomes suggested cables under Inventory › Cables ›
*Seen by switches*; it also reports whether each switch answers, and a host's Dashboard
shows its neighbours.

It starts with the hosts tagged `snmp`; change *Applies to* for more. A host is asked at
its primary address, or at the one its *Address for SNMP* says. Neighbours are matched to
hosts by the name they announce (any name labdex knows, with or without the domain), the
chassis MAC (an interface MAC in the inventory) or the management address. Ones labdex
doesn't know are listed too, with a button to add them as hosts.

Give the community or the v3 passphrases from the vault to share them between instances.
SNMPv3 passphrases need eight characters at least. Read-only: labdex never sets anything.

## Routing

OPNsense with FRR (its diagnostics API) or Cisco IOS-XE (RESTCONF). Lists BGP
neighbours (matched to hosts), routed prefixes that aren't an inventory network, and
inventory networks the router has no route for. A neighbour leaving Established
notifies.

## Traefik

Read-only, Traefik v2 or v3 API (`api.insecure`, or behind a router with basic auth).
**Import services from routers** plans a service per HTTP router with a `Host` rule: on
the host its backend points at, or, when the backend isn't in the inventory (a container
address), on the Traefik host with port 443/80. The router's host name becomes the
service's DNS name (`grafana.example.com.`, a full name), so the Services page links to
it. A Routers page shows each router, its backend and whether the inventory has it.

## Docker

Read-only Engine API, best through a socket proxy that only allows containers and the
version (e.g. `CONTAINERS=1`). **Import containers** plans:

- a host per container (`srv-ct-<service>-NN`, the container name as fun name), running
  on the Docker host, in its zone; an interface only where the container has an address
  in an inventory network (macvlan/ipvlan);
- services: published ports on the Docker host (that's where they listen), Traefik
  router labels as services with the router's host name on the container's host.

Turn off "containers as hosts" to get only services on the Docker host. **Read
container state** reports running / stopped as host status.
