# Technitium DNS

Checked against Technitium DNS Server 15.6. Use an API token limited to the zones
labdex manages.

- **Sync records** (plan): the inventory's records plus custom ones, into the closest
  zone on the server. Only records it created (marked in their comment) are changed or
  removed.
- **Split horizon** (setting, on by default): each public service name becomes an
  internal zone with the service's internal addresses. The zone is created as part of
  the plan.
- Per host: every record that belongs to it, flagged in sync, missing, stale or
  unmanaged.
- Records pointing at addresses that aren't in the inventory go into Discovery.
