# First steps

1. **Zones.** Under Zones create your areas (`DC` datacenter, `CA` campus, …); each
   gets a default zone. Add the zones you need inside them (`DC_TC`, `CA_UT`) with a
   rank and colour. "Load the example set" gives you a complete set to edit.
2. **Naming.** Under Naming, check the categories and types for hostnames
   (`<category>-<type>-<role>-<nn>`) and pick or paste fun name pools.
3. **Networks.** Add your VLANs with prefixes, gateways and DHCP ranges; each network
   sits in a zone.
4. **Hosts.** Add hosts with interfaces (the next free address is suggested) and
   services (port, protocol, link, who may reach them).
5. **Lint.** The Lint page tells you what breaks your conventions.
6. **Policy.** Fill in the zone matrix: which zone may reach which, on which ports.
7. **Plugins.** Add an instance of OPNsense, Technitium and others under Plugins,
   then run their actions. Changes come as plans you review on the Dashboard.

Or import a whole inventory at once: `labdex import inventory.yaml` (the format is
what `labdex export` writes; `examples/homelab.yaml` is a complete example).
