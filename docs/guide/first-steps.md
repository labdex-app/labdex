# First steps

On a new labdex the first page is a set-up in five steps. Only the first must happen there:

1. **Administrator**: the first account.
2. **Sites**: the site labdex started with, its name and optionally its git repository, branch
   and how changes go there (a merge request for every change, or a commit). *Save site*
   syncs once: an empty labdex reads the file from git; if the file isn't there yet, labdex
   writes it. A token for a private repository goes in the vault, then on the Sites page.
3. **Plugins**: the Marketplace; each plugin says what it may do before it's installed.
4. **Import**: networks and hosts from NetBox, phpIPAM or CSV files, with a preview first.
5. **Agents**: how a host enrols its agent from its Agent tab.

*Skip for now* and *Next* move on; each step has its own page for later.

Then:

1. **Zones.** Under Zones create your areas (`DC` datacenter, `CA` campus, …); each
   gets a default zone. Add the zones you need inside them (`DC_TC`, `CA_UT`) with a
   rank and colour. "Load the example set" gives you a complete set to edit.
2. **Naming.** Under Naming, check the categories and types for hostnames
   (`<category>-<type>-<role>-<nn>`) and pick or paste fun name pools.
3. **Networks.** Add your VLANs with prefixes, gateways and DHCP ranges; each network
   sits in a zone.
4. **Hosts.** Add hosts with interfaces (the next free address is suggested) and
   services (port, protocol, link, who may reach them).
5. **Checks.** *Inbox › Checks* lists what breaks your conventions; pick a finding to see it
   beside the list with the other findings on the same object. A finding that is fine on
   purpose can be accepted with a reason (administrators): it stays listed under *Accepted*,
   but stops counting in `labdex lint` and on the Overview.
6. **Policy.** Fill in the zone matrix: which zone may reach which, on which ports.
7. **Plugins.** Add an instance of OPNsense, Technitium and others under Plugins,
   then run their actions. Changes come as plans you review on the Dashboard.

Or import a whole inventory at once: `labdex import inventory.yaml` (the format is
what `labdex export` writes; `examples/homelab.yaml` is a complete example).
