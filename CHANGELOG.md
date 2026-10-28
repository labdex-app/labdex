# Changelog

## 1.24.1-1

### Fixes
- 

## 1.24.1

### labdex moved to labdex-app
- labdex, its Marketplace and the official plugins now live in the GitHub organization
  **[labdex-app](https://github.com/labdex-app)**. Old links redirect.
- **The image is now `ghcr.io/labdex-app/labdex`.** Change it in your compose file once:
  `ghcr.io/reykimedev/labdex` stays at 1.24.0 and gets no more updates.
- The official Marketplace is at `https://labdex-app.github.io/labdex-plugins/index.json`;
  labdex moves its official repository there by itself. The docs are at
  <https://labdex-app.github.io/labdex/>. An instance that still has the old project address
  (Settings › General) gets the new one; one you set yourself stays.

### Faster
- The services list is about 9× faster with 1000 hosts: permission checks no longer build
  each host's full record (the same for DNS, racks, Discovery and the audit log).

### Plugins come from the Marketplace only
- **No plugin ships with labdex any more**, the official ones included: the image and the
  wheel are labdex alone, and every plugin is installed from **Plugins › Marketplace** (or
  uploaded), downloaded and signature-checked like any other.
- **Backups are part of labdex**: the backup plugin moved into the core, so every instance
  has its daily backup from the first start, as before. The Marketplace no longer offers it.
- **Upgrading from 1.24.0**: labdex installs the official plugins you had (installed, or with
  an instance) from the official Marketplace by itself, at their held version if you held
  one, a few seconds after the start. Offline, it tries again every 15 minutes, and the
  Plugins page lists what's missing with an **Install them now** button; instances keep
  their settings and history meanwhile. A plugin you uninstall isn't put back.
- The demo only has the plugins of its own image (the demo lab and backups).
- For development: the official plugins are git submodules in `plugins/` (not in the
  package); `LABDEX_DEV_PLUGINS` loads such checkouts, the tests set it.

### Fixes
- labdex 1.24.0 didn't start when the official Marketplace had been added by hand before
  (under its name or address): it now takes that entry over. A problem setting up the
  official repository can no longer stop labdex from starting.
- The release's source archive is labdex alone (it included the plugins).

## 1.24.0

### Licence
- labdex is now under the **GNU AGPL-3.0** (was MIT), with a plugin exception: free to use
  and change, but a changed labdex, also one run as a service, has to stay open source and
  keep the credit to the original (see `NOTICE`). Plugins can use any licence; the official
  ones are AGPL-3.0 too. Earlier releases stay MIT.

### Patch panels and cable traces
- An interface can **pass through** to another port of the same host: a patch panel's front
  port and its rear port (set on one side, both get it). *Several* adds a whole panel at once,
  every port with its rear port.
- **Trace** follows a cable end to end through any number of patch panels, with each cable's
  label, colour and length: on a host's ports, on the Cables page and on a topology cable.

### Where is this MAC?
- Discovery › **Where is…** finds the switch port behind a MAC, an IP address or a host, from
  the switches' forwarding tables; the likely port is pinned (an uplink to another switch
  isn't). Typing a MAC or an address in the search offers it too, and a host's Network tab
  shows the port next to each interface that has no cable on record.
- **SNMP neighbours 1.1** reads BRIDGE-MIB and Q-BRIDGE-MIB (per VLAN); **UniFi 2.2** reports
  every client's switch port (wired) or access point (Wi-Fi). Publish both from their
  repositories.
- For plugin authors: `ctx.discovery.record_macs(source, entries)`.

### Sign in with OpenID Connect
- Authentik, Keycloak, Authelia, Pocket ID, Zitadel …: authorization code flow with PKCE,
  accounts created at the first sign-in (or only existing ones), groups from the provider
  matched to labdex groups, administrators by group, the password form optional. Configured
  through `LABDEX_OIDC_*`; Settings › Users shows the setup and the redirect URI.

### The top bar
- Help, the project's page, About, Settings and your account sit in a bar at the top; the
  sidebar lost its duplicate Collapse, Admin and avatar. **Admin is now Settings.** A **Help**
  page has the docs, issues, security reports, the keyboard and the details for a bug report.

### Release check
- The update check works with **GitHub, GitLab and Gitea/Forgejo** (Codeberg …) from the
  project's address (Settings › General; default github.com/reykimedev/labdex), says why a
  check failed, and tries again after ten minutes instead of six hours.

### CI
- Pushes get a quick lint; the full tests run on pull requests.
- Release images are built on native amd64 and arm64 runners in parallel (no emulation) and
  joined into one multi-arch image; each architecture keeps its own build cache.
- Dependabot keeps GitHub Actions, npm, Python, the base image and the plugin submodules up
  to date, as weekly pull requests.

## 1.23.1

### Faster image builds
- The UI is built once on the runner's own platform instead of again under arm64
  emulation; system packages and Python dependencies are a layer that only changes with
  `pyproject.toml`; layers are cached in the registry (`:buildcache`) between releases.
- CI uses actions that run on Node 24.

## 1.23.0

### Plugin versions and updates
- A plugin's details in the Marketplace show **what's new** since your version and
  **every version** its repository offers, with release notes and dates. Install any of
  them, newer or older.
- **Switch back**: an update keeps the version it replaced, so going back is one click
  (and works offline). Going forward again works the same way.
- **Stay on a version**: a held plugin gets no update offers and never updates by itself.
- **Withdrawn versions**: publishers can withdraw a version with a reason. Whoever has it
  is warned in the Marketplace and by notification, and updates skip it.
- **Update checks** every six hours (Admin › Plugins › Updates): *Tell me* (the default)
  sends a notification per new version once; *Install official updates* installs updates
  signed with the labdex project key by themselves, into History; *Off*. New notification
  events: `plugins.update`, `plugins.updated`, `plugins.update_failed`, `plugins.withdrawn`.
- Official plugins have a CHANGELOG, shown in the Marketplace.

### Plugins in their own repositories
- Every plugin that ships with labdex moves to a repository of its own
  (`labdex-plugin-<id>`), included in labdex as a git submodule. Tagging a version there
  signs it and attaches it to a GitHub release.
- The official Marketplace is its own repository, **labdex-plugins**: it lists the plugin
  repositories, checks every release's signature and publishes a signed index at
  reykimedev.github.io/labdex-plugins, which labdex trusts out of the box.
- For publishers: `labdex plugin build-index` (a Marketplace from plugin repositories),
  `labdex plugin yank` (withdraw a version), `labdex plugin fetch-repo` (keep older versions
  when rebuilding a repository), `tools/plugin_repo/make_plugin_repo.py` (start a plugin
  repository with its release workflow). An index can now list several versions of a
  plugin and point at archives anywhere.

### Releases
- labdex and every plugin are released by **publishing a GitHub release** (tag
  `v<version>`); the workflow checks the tag against the code with a clear message,
  builds, signs and attaches everything to that release.
- A release's description gets that version's CHANGELOG section (below anything written
  by hand), and `CHANGELOG.md` is attached. The source archive includes the plugins.
- Plugin repositories: `.releaseignore` keeps files out of the release zip
  (`labdex plugin prune`); the Marketplace finds new `labdex-plugin-*` repositories by
  itself and checks for new releases every hour.

### Also
- README, docs and the About page say plainly that labdex was built with AI and is meant
  for home labs only.
- Links point at the real addresses (github.com/reykimedev/labdex, its Pages and ghcr).

## 1.22.0

### Topology
- Cables are drawn in their own colour. Switch to colouring by speed on the legend
  (remembered in your browser and in saved views); thicker is still faster. White and black
  cables get a thin edge so they show on both themes.
- A host's fun name is a label of its own above the host, in bold; the hostname sits below
  in mono, quieter. Drag the fun name wherever it reads best: it stays there (shared, with
  the layout) and moves with the host.
- Port names are small tags further out along the cable, clear of the host and its name.
- **Snap to grid works**: the dots were drawn half a step off the points things snap to.
  The dots are a little easier to see too.
- Hypervisor boxes leave room for their guests' names.

### Policy
- Network › Policy has a second tab, **On the firewalls**: every firewall that enforces the
  matrix shows its pending rule changes, the rules no policy explains and which zones labdex
  manages, with **Sync policy rules** right there. It replaces the OPNsense instance's
  *Firewall policy* tab (old links land on the new tab).
- For plugin authors: `place: policy` on a page (with an `action`) puts it there.
- OPNsense plugin 1.3.

## 1.21.0

### Plugin Marketplace
- **Plugins › Marketplace**: every plugin that ships with labdex and everything your
  repositories offer, in one place: search, categories, *Not installed* / *Installed* /
  *Updates*, and a details view with what each plugin brings (settings, actions, pages,
  where it comes from). Installing a plugin that ships with labdex is one click and
  downloads nothing; a download from a repository is signature-checked and confirmed as
  before.
- **Not everything is installed by default.** A new labdex starts with Backup, File
  exports, Ansible runner and nmap. An upgrade keeps those plus every plugin you have an
  instance of, so nothing you use goes away.
- Uninstall a plugin that ships with labdex from the Marketplace (refused while it has
  instances). It stays part of labdex; install it again any time.
- A plugin's page says when it isn't installed, with an Install button.
- Admin › Plugins keeps upload, repositories and signing keys; the repository list now
  sends you to the Marketplace to install.
- The Marketplace view is open to everyone who can see integrations; installing needs an
  administrator, as before.
- An inventory import keeps the instance's plugin choices (and the official-repository
  setting) instead of resetting them.

## 1.20.0

### Cables the switches see
- Inventory › Cables › **Seen by switches**: cables switches report that aren't on record
  yet (tick and add, or ignore for good), ports where a switch sees something else than the
  record (keep it, or use what's seen), and neighbours labdex doesn't know (add them as
  hosts, prefilled). A cable both ends report shows once; adding one goes through the
  host's normal checks and into History.
- New plugin **SNMP neighbours**: reads LLDP-MIB and CISCO-CDP-MIB over SNMP v2c or v3
  (SHA, SHA-256, SHA-512, MD5; AES-128), reports whether each switch answers and shows its
  neighbours on the host. It starts with hosts tagged `snmp`. Its SNMP client is labdex's
  own (no new dependency) and is tested against net-snmp.
- **UniFi 2.1**: signed in with a local account it also reads LLDP tables, device uplinks
  and the switch port of every wired client.
- Ends are matched to hosts by any name labdex knows, an interface's MAC, or an address;
  port names match whatever their case, and the cable uses the host's own spelling.

### For plugin authors
- `ctx.discovery.record_links(source, links)` reports what a device sees on its ports.
- `applies_to` in a manifest: what a new instance applies to (the default stays `*`).

## 1.19.0

A UI pass: every page looked at in light, dark and on a phone.

### Fixed
- A network's page showed its id (GPUUVNFHZD) as the title instead of its name.
- A host's Plugins tab was blank when no plugin works per host; it says so now.
- A host's DNS records ran off the screen on a phone.
- The Overview dashboard had empty stretches and cut off *Last changes*; its default
  layout is tighter (updated for everyone who never changed it).
- A plugin page's title (and any name set in mono) was smaller than every other title.

### Controls
- The last radio buttons and tick lists are gone: the home zone (follow the primary
  interface / choose), a secret's source (own value / from the vault) and a plugin per
  host (follow / always on / off) are segmented controls; network pickers in plugin
  settings are chips; yes/no columns in plugin tables are switches. Tick boxes stay only
  where you pick rows to act on.
- One rule for buttons: one primary button per screen (what the page creates); adding
  inside a section is a normal button.
- Plugin actions all look alike, with an icon for *plans first* or *runs right away*
  (purple and grey ones read as enabled and disabled).
- A colour picker's "any colour" button is a rainbow swatch; a picked custom colour gets a
  swatch of its own.

### Layout
- Host: the rack on its own line with unit and side below; on a phone the hostname parts
  sit two by two; the home zone picker isn't squeezed.
- Network: a prefix's gateway and DHCP range on one line, its description below.
- Hosts on a phone: function (and a status other than active) under the name, so the
  name column has room.
- Topology: the toolbar fits one line; the speed legend sits on the map; PNG, SVG and
  Arrange again are under ⋯.
- Zones: rank and counts line up whether or not a zone can be deleted.
- Discovery: a count of zero is no longer red.
- Links in tables are coloured and underlined only on hover, everywhere.
- Backup plugin 1.2: the folder or the S3 settings, whichever *Where to* says.

## 1.18.0

### One place for secrets
- **The vault holds every shared secret.** Admin › Secrets is gone: its secrets move into
  the vault as `global/<name>` (field `value`) the first time an administrator sets the
  vault up or unseals it, written to History. Nothing that pointed at them breaks: a bare
  name keeps meaning `vault:global/<name>#value`, and `/api/secrets` works on those entries.
- **Plugins point at vault fields**: a secret setting offers *from the vault* with every
  field you may use (`fw/opnsense#key`). **Ansible variables** too:
  `{$secret: vault:fw/opnsense#key}`. The value is read when the job runs.
- **Pointing at a secret needs `secret:apply`** on it (the right to reveal it), since it
  hands the secret to a plugin or to Ansible. References someone else saved stay when you
  edit. New endpoint `/api/vault/refs` lists what you may point at.
- An entry shows **who uses it**; it can't be deleted while anything does, and **renaming
  it takes the references along**.
- While the vault is **sealed**, jobs that need a secret fail and say why.
- Lint reports variables that point at a vault field that doesn't exist.
- The public demo can't set up or change the vault.

## 1.17.0

### Cables
- A cable has a **label** (what's printed on it), a **length** in metres and a jacket
  **colour**, on every cable dialog. *Next free* continues the lab's own numbering
  (C-0042 → C-0043); a label belongs to one cable only.
- Inventory › **Cables**: every cable in one list, searchable, editable, with **printable
  labels**: two per cable, each naming this end and the other one.
- **Front panel**: a host with four ports or more shows them on its Dashboard and Network
  tab like the front of a switch (odd on top, cages apart), lit in the cable's speed colour.
  Point at a port for where it goes, click it for its cable.

### Topology
- **Saved views**: picture, zone, hidden networks, options and zoom under a name, for
  everyone, opened from the list or by link.
- **PNG and SVG** download of the whole picture.

### Fixed
- A cable inside one host survives a YAML import.

## 1.16.0

### Topology
- **Networks (L3)**: chips above the map hide and show single networks; a hidden
  network takes its lines along, and hosts left with none.
- Hosts show their **fun name with the hostname below**. A host can have **its own
  colour** on the map (Overview › Colour on the map); otherwise its zone's.
- **Grid**: a dot grid that pans and zooms with the map; what you drop snaps to it (a box
  keeps its guests' arrangement).
- **VIPs** are joined to the hosts that hold them in the L3 view, and left out of the
  physical one.
- **Port names** at the ends of cables (they fade out when you zoom far out). **Click a
  line** for its two ends and details; a cable's medium, speed and notes can be changed
  or the cable removed right there.
- **Point at a host** for a summary card: names, function, zone, status, address.
- **Guests on their hypervisor**: a guest interface that doesn't say what it sits on is
  joined to the hypervisor's bridge in the same network, drawn fainter as a guess.
- **Cables inside one host**: draw them on the map (click the same host twice) or add
  them on its Network tab (the host itself is offered as the far end). They show as a loop.

### Interfaces
- Physical and virtual interfaces are listed apart: ports first, then bonds, bridges,
  VLANs, tunnels and loopbacks under what they sit on.
- A physical port has a **connector** (RJ45, SFP, SFP+, SFP28, QSFP+, QSFP28, QSFP-DD …)
  and a **speed**; picking a connector offers its usual speed.
- **Several**: add many interfaces at once from a pattern: `eth[0-3]`, `1/0/[1-48]`,
  `swp[1-4]s[0-3]`, `Gi1/0/[01-24]`.

### And
- Sidebar sections open and close with a short slide; closed ones are out of the tab order.
- **Features from a dotted path**: making `test.test.test` makes `test` and `test.test`
  as well when they're missing (UI, API and YAML import), each with its own history entry.

## 1.15.0

### Vault
- Inventory › **Vault**: labdex's own store for root passwords, IPMI logins, Wi-Fi keys
  and recovery codes. Entries have a path (`hosts/srv-pm-pve-01/root`), an optional host,
  a description and named fields; a button makes a strong password in the browser.
- **Sealed start**: the vault is encrypted with a passphrase labdex never stores. After
  every start an administrator unseals it; until then names show and values don't. Five
  wrong passphrases a minute, then a pause. Seal it by hand any time; change the passphrase
  without touching the entries.
- Envelope encryption: scrypt and AES-256-GCM for the master key, a fresh data key per
  version, each ciphertext bound to its entry and version. A database copy or a backup
  alone opens nothing.
- Values show one field at a time, for 30 seconds, or are copied without showing; each
  reveal is in History. The browser asks for your password again before the first reveal
  and every 5 minutes after.
- Every change is a new version; the last 10 are kept and can be revealed.
- Hosts have a **Secrets** tab with their entries and *+ Secret for this host*; the host's
  rights apply to them and they follow the host through renames and YAML imports.
- New permission type `secret` and the built-in role **secret-keeper**. A `*` role or
  grant covers only viewing secrets: revealing (`secret:apply`) and changing
  (`secret:edit`) are always given by name. Path scopes work: `name:wifi/*`.
- Docs: *The vault* under Concepts, with how it's built.

## 1.14.0

### Racks
- Inventory › **Racks**: every rack as an elevation, front and rear side by side, grouped
  by site. Click a free unit to put a host there; a host's ⋯ moves it or takes it out.
  Devices on the front and the rear can share a unit; full-depth ones take both sides.
  Overlaps are refused with what's in the way.
- What isn't a host goes in too: patch panels, cable management, blank panels, shelves,
  PDUs, each with a label and a description.
- A host has a **Size** in units (U, HE) of its own, set on its Overview whether or not
  it's in a rack; putting it in a rack uses it. Its rack, unit and side are there too; the
  Dashboard tab shows where it sits and links to its rack.
- Racks are part of the YAML export and import; renaming a rack keeps its hosts.

## 1.13.0

A review release: tooling, dead code, fixes.

### Code
- ESLint (JavaScript and Vue rules) runs with `npm run lint` and in CI; ruff checks more
  (pycodestyle, bugbear, pyupgrade, comprehensions). Everything they found is fixed.
- Forms that edit an object handed to them (service, prefixes, plugin settings) use
  `v-model` instead of changing their props.
- Dead code removed: an unused ARP reader, YAML dumper, trust-level defaults from before
  zones, unused helpers and exports, a filter function and variables left over in views.
- The host's dashboard is its own component; shared styles (`.g2`, `.g3`, `.ell`,
  `.hide-m`) live once in the stylesheet instead of being copied into views.

### Fixes
- Reloading a page several times quickly could leave live updates refused for a while
  ("too many open event streams"); a new stream now replaces the oldest of yours.
- Tables inside cards no longer break words letter by letter (the aliases' type column).
- "Not on a phone" hints were shown on phones: the class only worked in the sidebar.
- Dashboard links refuse backslashes and line breaks too (`/\\host` is `//host` to a browser).
- The changelog renderer escapes quotes in links.
- The docs named a host status `retired`; it's `legacy`.

### Small things
- History hides sign-ins unless you tick *Sign-ins too*, and filters by more object types
  (zones, areas, tags, features, the zone policy).
- The aliases table names its last column (*Source*).

## 1.12.0

### Dashboards
- The start page is a dashboard of cards on a 12-column grid. *Edit* moves cards (drag the
  handle, or arrow keys), resizes them (the corner, or Shift + arrows), removes and adds
  them; *Done* saves. Cards: the numbers, approvals, failing and running jobs, last
  changes, coming up, hosts per zone, every plugin widget or a single one, your links,
  the inventory's services with their status (by tag, zone or category), and notes.
- More dashboards: your own (only you see them) and shared ones (administrators). A new
  one can start as a page of links: service links, your own links and a note.
- Link cards only take http(s) addresses or labdex pages; the server refuses anything
  else, the dialog says so before.

### Fixes
- Rows in lists of links (dashboard cards) aren't underlined like links inside a sentence.

## 1.11.0

### Hosts
- A host opens on its new **Dashboard**: status and monitors, addresses, zone, hypervisor,
  guests and cables, links to its services, and the plugin widgets (they moved here from
  Overview). The form is under Overview.
- Addresses per family in the interface dialog, like a network's prefixes: IPv4 address
  and more IPv4 addresses; IPv6 interface ID or address and more IPv6 addresses. The
  topology's network view shows the extra addresses too.

### Stable addresses
- Hosts, networks and zones have a short id that never changes, and pages use it in the
  address bar (`/hosts/7k2m9x4qa1`): bookmarks and shared links survive renames. Links by
  name keep working. The API takes the id wherever it takes a name.
- Zones have a page: networks, hosts at home there, and what the policy lets in and out.
  A renamed zone (or a zone whose area is renamed) keeps its id.

### Fixes
- An unsaved draft no longer hides the whole host or network page until you choose
  *Restore* or *Discard*; the page shows below the offer.

## 1.10.0

### Topology
- Two views: **Physical** (cables, hypervisors with their guests) and **Networks (L3 /
  VLANs)**: networks as nodes, every host joined to the networks it has an address in;
  routers and other multi-homed hosts are diamonds. Each view keeps its own shared
  arrangement.
- Hosts show their icon. Cables are coloured by speed and thicker when faster, with a
  legend of the speeds on the map; Wi-Fi links are dotted.
- **Draw cables on the map**: *Cable*, click both ends, pick each side's interface from a
  list (or name an unnamed one, or add a new port to the host). Saved on both hosts with
  history; a cable is removed from a host's side panel.

### Sidebar and forms
- One section open at a time; the section titles are as large as the entries. Detail
  pages mark their list (a host marks Hosts). *Collapse* at the bottom and Ctrl B shrink
  and expand the sidebar.
- Plugins no longer add sidebar entries: their pages are tabs on the integration. Old
  links keep working.
- On/off settings are switches; lists you pick several from keep tick boxes.
- Cable speeds are offered as a list (100M … 100G) and shown as a colour on the host's
  Network tab. VIP membership there is a section like interfaces and cables, with space
  between them.
- New hosts start as *active*.

### Zones
- Zone codes are names: type `Trusted clients` and the zone becomes `DC_TRUSTED_CLIENTS`.
  Areas take up to 16 letters or digits, zones up to 48 characters.

### Discovery
- nmap also checks the port of every declared TCP service on its host, even outside its
  port list. *Not answering* now means a scan looked at that port and found it closed; a
  port nobody scanned shows *not scanned* instead of being reported down (and isn't
  listed as missing).

## 1.9.0

### Versions
- labdex versions are now `MAJOR.MINOR.SUB`, and `MAJOR.MINOR.SUB-N` for the N-th hotfix
  of a release (bug fixes only; 1.9.0-1 comes after 1.9.0, the next release is 1.10.0).
  `tools/bump.py` writes a new version everywhere; the image of a hotfix also moves the
  tags of its release (`1.9.0`, `1.9`, `1`, `latest`).

### Hosts
- Hosts has three tabs: **Physical**, **Virtual** (guests of another host, VMs and
  containers) and **VIPs**. *Host* on the Virtual tab starts a guest.
- The hosts search takes terms: `tag:`, `zone:`, `status:`, `cat:`, `on:` (guests of a
  host) and `net:`, combined with plain words; several `tag:` terms must all match. A tag
  chip in the list adds its term.
- **Group by** tag, zone, runs on, category or status, with a heading and count per group.
- The search box (Ctrl K) offers *Hosts tagged …* for `tag:x` or a word that is a tag.

### Topology
- The map's arrangement is shared: everyone sees the same positions, and people who may
  edit hosts move them for all. The hint says who arranged it last; *Arrange again* starts
  over for everyone (after asking).

### Drafts
- Host and network forms keep what you type as a draft in this browser for two weeks.
  Coming back offers it again (*Restore them* / *Discard*); Save still applies, and clears
  the draft.

### Integrations
- **UniFi 2.0** signs in the way your setup allows: a local username and password on
  UniFi OS Server or a UniFi OS console, a username and password on the Network
  application on its own, a console API key, or a **Site Manager** API key from
  unifi.ui.com (every console the key sees, read through the cloud connector; devices
  still come from Site Manager when the connector can't reach a console). Accounts with
  two-factor sign-in get a clear message; labdex signs out after every run.
- **Proxmox**: *Check drift* puts guests the inventory doesn't have into Discovery, with
  address, name and MAC, ready to adopt.
- Plugin settings can depend on another one (`when: {auth: [password]}`): the form shows
  only what applies, and hidden settings are neither required nor lost.

## 1.8.0

### Light theme
- A light theme next to the dark one: Auto (follows the system), Light or Dark in the
  menu under your name, or `light` / `dark` in the search box. Kept in this browser and
  applied before the page draws, so it never flashes. Zone and tag colours are darkened
  in the light theme so they stay readable; the topology redraws in the theme's colours.

### Phones
- No page scrolls sideways any more at phone width: long words and URLs (error messages,
  for one) wrap, and grids no longer grow past the screen (the Dashboard was 1844 px wide).
- The hosts list keeps name, function, zone and status on a phone; addresses, links and
  monitoring columns come back on a wider screen.

### Accessibility
- An automated WCAG 2.1 AA check (axe) of 19 pages in both themes finds nothing:
  faint text, button text and zone chips meet 4.5:1 contrast; links inside sentences are
  underlined; the policy matrix scrolls with the keyboard; the address grid has the right
  role.
- Rows you click to open something (hosts, networks, jobs, history, …) are reachable with
  Tab and open with Enter. Dialogs keep Tab inside them. "Skip to the page" jumps past the
  sidebar. Tabs say which one is current. Inputs show a focus ring.
- The ✕ on fun name, VIP member and feature chips is a real button.

## 1.7.0

### Topology
- Network › Topology draws the lab: a host that others run on is a box with its guests
  inside and its own interfaces (bridges, bonds, ports) joined the way they're stacked;
  cables join ports; internal links run from a guest to the bridge it sits on. Filter by
  zone, find a host or address, click for its links, double-click to open it. Drag to
  arrange: kept in your browser. New endpoint: `GET /api/topology`.

### Internal cables
- A cable can be **internal**: between a guest and the host it runs on (or two guests of
  one host), typically the guest's NIC on the hypervisor's bridge. The cable dialog
  offers only those hosts and suggests the other side's interfaces. A bridge holds any
  number of internal links (a physical port still holds one cable).

### Address map
- A network's Address map tab: every address of its IPv4 prefix, a /24 at a time, as in
  use, gateway, DHCP pool, seen (by Discovery, not in labdex), conflict (two hosts, or
  Discovery saw another MAC) or free. Click a free one to make a host with it, a seen one
  to add it. New endpoint: `GET /api/networks/<name>/map`.

### Plans
- The job page groups changes by kind and shows, for an update, only the fields that
  change. Changes can be unticked before approving: the rest is applied, the log says who
  left out what (`skip` on `POST /api/jobs/<id>/approve`). Unticked changes stay unticked
  when a plan is made again with the same changes.

### Fixes
- A host or network page counted as changed while it was still loading, so leaving it
  right away asked about unsaved changes (and the button said "Save changes").

## 1.6.0

### Search and go
- Ctrl/⌘ K (or / outside a text field) opens one search box: hosts by name, fun name,
  function, tag, address or MAC (typed any way); networks by name, VLAN or an address
  inside them; services; aliases by name or firewall id; integrations, and the actions
  you may run there (Enter starts one and opens the job); pages; New host, VIP or
  network. Only what you may see. A Search button sits at the top of the sidebar.
- New endpoint: `GET /api/search?q=`.

### Many hosts at once
- Tick hosts on the list (Shift-click for a range, the header box for all shown) and
  change status, runs on, tags (add and remove) or an integration (on, off, as its
  selectors say) for all of them, or delete them. All or nothing; each host gets its own
  history entry with the same summary. New endpoint: `POST /api/hosts/bulk`.

### Keeping your place
- Back and forward return to the scroll position you left. DNS, Discovery and History
  are kept alive like the other lists (they refresh in the background when shown again).
- Ctrl/⌘ S saves a host or network; leaving one with unsaved changes asks first, also
  when closing the tab.
- A search link to an alias opens the Aliases page filtered to it.

### Smaller things
- One heading on the Zones page ("Areas and zones").
- Long notification messages are cut to two lines with "show all".
- Empty Networks, Services and DNS pages say what to do next.
- Docs: "Working fast" (shortcuts, search, bulk changes).

## 1.5.0

### Isolated plugins
- Community and unsigned plugins no longer run inside labdex. Each call (action, plan,
  apply, export, page, widget, endpoint, test) runs in a process of its own with an
  empty environment, which locks itself with Landlock before the plugin is loaded: it can
  read Python and the plugin's own folder, and write only to a data folder of its own and
  a temporary folder. It can't open labdex's database or `secret.key`, other plugins'
  folders, or labdex's memory and environment, and can't signal labdex.
- The plugin API reaches labdex over a pipe and answers for that instance only: its own
  settings and host settings, the inventory, state, host status, notifications and
  discovery records. Anything else is refused.
- Their Ansible playbooks (in code or manifest-only actions) run inside the same lock;
  global secrets aren't filled in for them.
- labdex's own process is non-dumpable, so no plugin of the same user can read its
  memory, isolated or not.
- `LABDEX_PLUGIN_SANDBOX`: `on` (default), `strict` (refuse isolated plugins without
  Landlock), `off` (run them inside labdex, as before). The Plugins page shows which
  plugins run isolated and how isolated they are on this machine; the install dialog says
  what an unsigned plugin will be able to reach.
- Official and built-in plugins run inside labdex as before.

### Plugin API
- `ctx.data_dir` can also be called (`data_dir()`, as the reference always said).
- Ansible runs with a local connection keep their temporary files in the run's folder.

## 1.4.0

### Proxmox: creating guests
- Plan a VM or container in the inventory (a planned host with its address, on a
  Proxmox node) and labdex creates it in Proxmox, as a plan you approve: **Create
  planned guests** for all of them, **Create this guest** on one host. Off until
  "Create guests" is turned on in the instance.
- Containers come from an LXC template, VMs are cloned from a cloud-init template.
  Network, VLAN tag, static address and gateway (or DHCP), IPv6 (static or SLAAC), SSH
  keys, DNS server and search domain come from the inventory and the instance; cores,
  memory, disk, storage and template per host override the instance's defaults.
- A MAC the inventory lacks is generated with Proxmox's prefix, the same in every plan,
  and written back to the host. The host is linked to its guest and set active once
  it runs. A guest that was created but failed a later step is still linked (and the
  host set offline), so it's never created twice.
- Nothing is ever deleted or changed in Proxmox after creation.

### Fixes
- Proxmox: saving a host's Proxmox settings without a guest id stored id 0, which
  counted as linked to a guest.

## 1.3.0

### Plugins
- Every plugin labdex ships is now an official plugin: Ansible, Backup, Certificates,
  Docker, File exports, k3s, MOTD, nmap, OPNsense, Prometheus SD, Proxmox, Routing,
  Tailscale, Technitium, Traefik, VictoriaMetrics and YAML mirror join Beszel, Uptime
  Kuma and UniFi. They still work from the first start, and now get fixes from the
  official repository between labdex releases. Plugin ids are unchanged, so integrations,
  settings and history carry over. Only the demo lab stays built in.
- Each states the labdex versions it works with (`>=1.3,<2`).
- Plugins page: a shipped plugin has no Remove button; an installed update of one has
  "Remove update", which goes back to the shipped version.
- Only an update signed with the labdex project key can take a shipped plugin's place,
  at install and when loading: an unsigned or community copy with the same id is refused
  (labdex keeps the shipped one). Before, ticking "I trust its author" let an unsigned
  copy stand in for Beszel, Uptime Kuma or UniFi.
- `labdex.discovery.network_of` (the network an address belongs to) is public; the old
  `_network_of` name still works.

## 1.2.0

A UI overhaul: one look across every page, fewer things on screen at once, and forms in
dialogs instead of inline.

### Look
- IBM Plex Sans and Mono, a calmer dark palette, one control radius and one box radius,
  sentence-case headings and labels everywhere (no more all-caps table heads, tile and
  widget labels), and line icons (Lucide) for actions instead of "Edit", "✕" and "+ …".
- Lists of things (interfaces, cables, services, zones, tags, hostname parts, fun name
  pools) are rows with a title, a muted detail line and icon actions on the right.
- Filters are compact "Label: value" selects on every page (Hosts, Aliases, Services,
  Discovery, DNS, Lint, History, Plugins) instead of rows of chips.

### Navigation
- The sidebar is grouped into Inventory, Network, Automation and Audit, each collapsible;
  the group of the current page opens by itself, and a collapsed group shows its badge.
  The zone tree is its own collapsible section. What's open is remembered.

### Dialogs
- Create and edit happen in dialogs (Esc or a click outside closes them): areas and
  zones, aliases, tags, hostname parts, fun name pools, policy cells, job templates,
  approval policies and change windows, notification channels, users, groups, roles,
  role bindings, object grants, secrets, plugin instances and author keys.
- Every "are you sure?" is a labdex dialog that says what happens, not the browser's.

### Hosts
- The Network tab is a list of interfaces as a tree (bond → bridge → ports), each with
  its zone, addresses, MAC, cable and what Discovery saw; editing one opens a dialog
  with next-free buttons, extra addresses and per-prefix IPv6. Cables are their own list.
- The Services tab lists services and host rules; a service is edited in a dialog with
  allow-from as chips with suggestions.
- The hosts list shows at most three addresses ("N more").

### Tags
- Tag colours show everywhere a tag does: the hosts list, the host form's tag input and
  the tag list; the tag dialog has the same colour swatches as zones.

## 1.1.0

### Traefik
- New built-in plugin (read-only, Traefik v2/v3 API, optional basic auth). "Import
  services from routers" plans a service per HTTP router with a `Host` rule: on the host
  its backend points at, else on the Traefik host (port 443/80). The router's host name
  is the service's link; path prefixes and TLS (router TLS or a websecure entry point)
  carry over. Filters for entry points and providers; a Routers page and a dashboard
  widget.

### Docker
- New built-in plugin (read-only Engine API, best behind a socket proxy; optional bearer
  token). "Import containers" plans a host per container (`srv-ct-<service>-NN`, fun name =
  container name, runs on the Docker host, in its zone; an interface where the container
  has an address in an inventory network), and services: published ports on the Docker
  host, Traefik router labels with their host names. Existing hosts are linked by name;
  nothing is removed. "Read container state" reports running/stopped as host status.
  Options: services only (no container hosts), stopped containers, ignore patterns,
  category. A Containers page and a dashboard widget.

### Services
- A service's DNS name can be a full name ending in a dot (`grafana.example.com.`): the
  link uses it as is, and it gets a CNAME only when it lies in a zone labdex writes.
- Plugins share `labdex.plugins.servicesync` to plan and apply services found elsewhere:
  matched by DNS name, then port, then name; only adds and fills in, never removes.

### Fixes
- Plugin HTTP clients took any path starting with "http" (like Traefik's `http/routers`)
  for a full URL.

## 1.0.0

labdex is stable: within 1.x the plugin API, the REST API, the inventory YAML and the
backup format only grow, and every 1.x starts on a database from any earlier release
without a manual step.

### Plugins
- **Bundled official plugins**: Beszel, Uptime Kuma and UniFi ship with labdex as official
  plugins that update on their own. When the official repository has a newer version,
  the marketplace offers it; the update stands in for the bundled copy, a broken update
  never does, and removing the update brings the bundled version back.
- **The official repository** is there out of the box (removing it is respected), built
  from `backend/labdex/official_plugins` and signed with the project key by the docs
  workflow. `LABDEX_OFFICIAL_INDEX` points elsewhere or turns it off.
- The plugin API reference (`docs/develop/plugin-api.md`) lists what stays stable; plugins
  name the versions they work with, e.g. `labdex: ">=1.0,<2"`.

### Docs, releases
- A docs site (MkDocs Material, `docs/`), published with the official repository to
  GitHub Pages by `docs.yml`.
- Releases: multi-arch images signed with cosign (keyless) with an SPDX SBOM attestation;
  a GitHub release with the source archive, wheel, sdist, SBOMs, a cosign bundle per file
  and signed checksums. How to verify: docs → Security.

### Security review
- Sign-in throttling counts the real client behind a trusted proxy (one client can't lock
  a user out for everyone behind it) and also limits failures per user overall; the
  failure table can't grow without bound. History records the client's address.
- Event streams check every 15 seconds that the user is still active, the session's
  password unchanged and the API token valid, and a user can hold at most 8 at once.
- Plugin archives are measured while unpacking (a zip can't lie its way past the size
  limit); restoring a backup caps the unpacked size.
- `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and HSTS when cookies are secure.
- The review itself: `docs/operate/security-review.md`.

### Upgrades
- The 1.0 gate is a test: a database with the 0.5.0 schema (the example lab, trust levels,
  an admin) starts on 1.0, is migrated, and answers every page; nothing on the firewall is
  renamed (`tests/test_upgrade_gate.py`).

## 0.10.5

### Firewall
- **Alias uids**: aliases are called `ldx_` and 8 hex digits on the firewall (stable, from
  the labdex name); the full name stays in the description. New instances use uids; an
  instance that already has an inventory keeps readable ids until you switch under
  Admin → Conventions → Firewall ids. Switching renames labdex's aliases on the firewall:
  sync aliases, then rules; your own rules that use the old names need updating (the
  policy page lists them).
- The Aliases page shows the labdex name and the firewall id in their own columns and
  pages through 50 at a time.
- **Import the firewall's aliases** (OPNsense action, optionally limited by name
  patterns): your own aliases become custom aliases in labdex under their names on the
  firewall, so nothing is renamed; members that name other aliases become alias
  references. labdex's and OPNsense's built-in aliases, unsupported types (GeoIP, …) and
  ones labdex already has are skipped. Turn on "take over" to let labdex manage them.
- **Allow from** picks from zones, areas, networks (with their prefixes), prefixes,
  custom and generated aliases as you type; addresses and CIDRs are fine too, unknown
  sources are marked.
- **Firewall rules for the whole host** (Services tab): allow from a zone, area,
  network, alias, address or any, or allow to one, on ports (any, port aliases,
  tcp/443). "Sync policy rules" turns each into pass rules (`[host:…]`, category
  `automation:labdex`). YAML: host `rules`.

## 0.10.4

### DNS overview
- New **DNS** page: every record labdex generates (name, type, value, zone, host), and per
  DNS server (Technitium) every record in the zones it reads, each marked in sync,
  missing (the next sync creates it), stale (labdex made it, the next sync deletes it) or
  unmanaged. Filter by status and zone, search, 50 per page. Records of hosts you can't
  see are left out. API: `GET /api/dns`; DNS plugins provide `dns` and a
  `server_records()` method.

### Discovery output
- nmap's progress goes into the job log as it scans: each open port as it's found,
  "x% done" every 15 seconds, then one line per host with its open ports.

## 0.10.3

### Interfaces, cables, vendor, icons
- Interfaces have a **name** (eth0, vmbr0, ge-0/0/1; was "label"), a **type** (ethernet,
  wifi, bond, bridge, vlan, virtual, wireguard, loopback, other) and **more addresses**
  (secondary IPs, as chips). Secondary addresses go into the host's firewall aliases,
  count as used in IPAM and in Discovery, and get no DNS records.
- **Logical stack**: an interface can sit on another one: a VLAN on a bond, bond members
  on the bond, a bridge on a NIC, a VM's NIC on its hypervisor's bridge
  (`srv-pm-pve-01:vmbr0`). Lint warns when a VM NIC sits on a bridge that doesn't exist.
- **Cables**: port here ⟷ other host's port, with medium (copper, fiber, DAC, Wi-Fi,
  virtual), speed and notes. A cable is stored once and shows on both hosts; the other
  end needn't be a known interface (switch ports usually aren't). Plugging a port that
  already has a cable moves it. The Network tab draws the stack with its cables.
- Hosts have a **vendor**, a **model** and an **icon** (twenty kinds of device; by default
  from the type part of the name: vm, pm, fw, sw, ap, nas, …). Hosts are listed with their
  icon. Assigning a discovered address to a host fills its vendor (from the MAC) when
  empty, and "New host" from Discovery prefills it.
- The Network tab shows, per address, what Discovery saw: name, vendor, MAC, source and
  when, with one-click "use as vendor" and "use" for the MAC. API: host `seen`.
- YAML: interface `kind`, `parent`, `extra_ips`; host `vendor`, `model`, `icon` and
  `cables` (each cable once).

## 0.10.2

### Tags, naming lists, hosts
- **Tags** are known everywhere: hosts pick them from a list as chips (type to filter,
  Enter or comma to add, Backspace to remove); typing a new one adds it to the list.
  Naming → Tags lists them with colour, description and how many hosts carry them;
  renaming follows every host and `tag:` role scope, deleting removes the tag from its
  hosts. Existing host tags become the list on upgrade. YAML: `tags:` (those with a
  colour or description). API: `/api/tags`.
- **Naming** has tabs (hostname parts, fun name pools, tags) and a search. Long lists show
  the first 12 with "Show all"; pools page through 15 at a time and show the names as
  chips, used ones struck through, matches highlighted while you search.
- **Hosts**: Devices and VIPs in their own tabs (with counts; "+ VIP" opens the form as a
  VIP), and a tag filter.
- The sidebar's zone tree scrolls under its pinned "Zones" header and search box.
- Widget errors wrap and stay short (full text on hover).

## 0.10.1

### Zones made one by one
- An **area** is where something is; **zones** are made one by one inside it, each with a
  name, a rank (higher = more trusted; empty = none), a colour and a description. No more
  grid of every area × trust level.
- Every area has a **default zone** with the area's own code (`DC`) for networks, hosts
  and interfaces you don't classify further. It has no rank, so the placement check
  leaves it alone. On the firewall it is `NG_ZONE_DC_DEFAULT` (`NG_ZONE_DC` stays the
  whole area).
- **Colours**: pick one per area and, optionally, per zone (otherwise the area's). Chips,
  the sidebar and the matrix use them; the prompt and banner get the nearest terminal
  colour.
- Renaming an area or zone renames it on networks, hosts, alias members, zone policy
  cells and role scopes. A zone in use, or named in the policy, can't be deleted.
- Upgrading: every area gets its default zone, and each area × trust level combination in
  use becomes a zone named "<area>, <trust level>" with that level's rank, colour and
  description. Unused combinations are dropped, and so are trust levels.
- YAML: `zones:` (code, name, rank, colour, description) and `colour` on areas. Files
  with `trust_levels:` still import, the same way as the upgrade.
- The host page picks the home zone from one list grouped by area.
- API: `/api/zones/zones[/<code>]` replaces `/api/zones/trust`; `/api/meta` zones carry
  `area`, `rank`, `default` and `hex`.

## 0.10.0

### Fixes
- Feature variables: lists now merge (the parent's items first, then the ones a child
  adds) instead of the child's list replacing the parent's. `{$replace: [...]}` still
  replaces a list or mapping outright.
- MAC addresses in Discovery come only from ARP/NDP tables and DHCP leases (OPNsense,
  Kea, UniFi). nmap no longer records them: behind a container bridge or a router
  it sees the wrong MAC. MACs that scans learned before are forgotten on upgrade.
- Technitium: Conditional Forwarder zones are found and written like primary zones,
  so records go into e.g. `mgmt.local.example.net` when that is a forwarder zone.
- Next free address skips addresses Discovery has seen (scans, ARP/NDP, leases, DNS),
  IPv6 interface ids included.
- Custom records may be wildcards: `*.primary.docker` (only as the leftmost label).
- Tabs no longer show a vertical scrollbar.

## 0.9.4

### Demo mode
- `LABDEX_DEMO=1`: loads a sample lab (networks, hosts, services, aliases, a zone
  policy) into an empty database and again every night at 03:00 UTC, with a `demo`
  admin whose password is on the sign-in page and a banner saying when it resets.
- The built-in *Demo lab* plugin, only there in demo mode, is a pretend DNS server
  (plan, approve, apply) and monitor (one host has a bad day), with dashboard and
  host widgets. Nothing in the demo reaches a network: other plugins can't be added
  or run, `ctx.http` and Ansible refuse, and plugin installs, repositories,
  notification channels, secrets, restores, user and password changes are off.

## 0.9.3

### Plugin compatibility
- `labdex:` in plugin.yaml names the labdex versions a plugin works with, as a PEP 440
  range (`">=1.0"`, `">=1.0,<2"`). labdex refuses to install a plugin outside its range,
  doesn't load an installed one that falls outside after an upgrade, and says why. No
  range: assumed to work.
- Repository indexes carry the range; the marketplace shows it and doesn't offer plugins
  that need another labdex. The plugin page shows "works with".
- From 1.0 on the plugin API only grows within a major version (see the README).

## 0.9.2

### Public services
- A service can be public: tick it, give it a public name (`dns.example.com`) and a WAN
  port. Public services need a port; lint warns when one lives outside a DMZ zone and
  notes a public service without a name.
- OPNsense "Sync port forwards" plans one destination NAT rule per public service (WAN
  address and port → the host's address and the service's port), in the category
  `automation:labdex`, keyed `[nat:host/service]`. Rules you made yourself are never
  touched. The WAN interface and destination are settings.
- Technitium split horizon (setting, on by default): each public name becomes a zone on
  the internal server with the service's internal addresses, so inside the house the
  name resolves to the host and not to the WAN address. The zone is created as part of
  the plan. Public DNS stays with whoever hosts it; labdex doesn't touch it.
- The policy page of an OPNsense instance has a Zones table: per zone, the labdex
  rules, other pass rules on its interfaces and access-check gaps. A zone with only
  labdex rules and no gaps is "managed by labdex". "Check access" logs those zones.

## 0.9.1

### Firewall rules from the zone matrix
- OPNsense "Sync policy rules" (plan/apply) turns every matrix cell into pass rules on
  the source zone's interfaces, between the zones' aliases, with port aliases or
  `tcp/443`-style ports. A service's *allow from* that the matrix doesn't already cover
  gets a rule of its own. All of them live in the category `automation:labdex`, keyed in
  the description; nothing else on the firewall is changed.
- "Rules no policy explains" lists your own pass rules between zones that the matrix
  doesn't allow. It's on the instance's Firewall policy page.
- The rules are re-planned after inventory and policy changes, and wait for approval.

## 0.9.0

### Zone policy
- New Policy page: a matrix of zones, rows where traffic comes from, columns where it
  goes. A cell holds ports (`any`, port aliases, `tcp/22`, `udp/53`, ranges) and a
  reason. Anything not listed is denied.
- Everyone can read it; admins edit it. Changes are recorded in History (type
  `zone_policy`), emit `policy.updated` and are part of the YAML export and import
  (`zone_policy`).

## 0.8.4

### Routing discovery
- New built-in plugin for OPNsense with FRR (its diagnostics API) or Cisco IOS-XE
  (RESTCONF: the BGP operational model and the ietf-routing table). "Read routes and
  neighbours" lists BGP neighbours (matched to hosts), the routed prefixes that aren't
  an inventory network, and the inventory networks the router has no route for.
  Default and host routes are left out; prefixes that are fine without a network can
  be ignored.
- A neighbour that drops out of Established sends `plugin.routing.neighbour_down`.
  A Routing page and a dashboard widget show it.

## 0.8.3

### UniFi Network
- New built-in plugin using UniFi's integration API (an API key, UniFi Network 9 or
  newer). Clients go into Discovery with address, MAC and name; UniFi devices
  (switches, access points, gateways) that are in the inventory report online or
  offline as host status. A dashboard widget lists the devices.

## 0.8.2

### Tailscale and Headscale
- New built-in plugin. "Read devices" puts every tailnet device's addresses into
  Discovery, so unknown ones can be assigned to hosts (e.g. in a 100.64.0.0/10
  network in your remote-access area). Devices that are hosts report online as
  status; offline counts as no data unless you choose "down".
- Widgets: the tailnet on the dashboard; a host's device, addresses, OS and key
  expiry on its page.

## 0.8.1

### k3s / Kubernetes
- New built-in plugin, read-only with a service account token. "Read nodes" matches
  nodes to hosts, reports Ready / NotReady as host status, and lists drift against the
  cluster's feature (hosts with the feature that aren't nodes, nodes without it),
  nodes that match no host, and mixed kubelet versions. A Cluster page, a dashboard
  widget and a node widget on the host page.

## 0.8.0

### Proxmox VE (read-only)
- New built-in plugin with a read-only API token (PVEAuditor). "Import guests" plans
  one host per VM or container labdex doesn't know: a guest named by the convention
  keeps its name; any other gets `srv-vm-<name>-NN` (`ct` for containers) with its
  Proxmox name as a fun name. It brings the node as "runs on", the MACs, the VLAN's
  network and the addresses from the guest agent (VMs) or the container. Guests that
  match an existing host by MAC, address or name are linked to it instead. Templates
  are skipped unless you want them. Applying records each import in History.
- "Check drift" (hourly by default) compares every linked guest with its host: name,
  MACs, addresses, node, and running vs. the host's status, and lists guests not in
  the inventory. A host setting "Known differences" explains drift, which then
  doesn't count. A Proxmox page, a dashboard widget and a host widget show it.
- labdex never changes anything in Proxmox; creating guests comes after 1.0.

## 0.7.5

### Certificates
- New built-in module. "Check certificates" (twice a day by default) connects to every
  https service in the inventory (by its link, or its address) and to other endpoints
  you list (`mail.example.net:993`), reads the certificate even when it isn't trusted,
  and records who it's for, the issuer, the expiry and whether it verifies.
- Warns at 21 days and calls it critical at 7 (both adjustable): a
  `plugin.certificates.expiring` notification once per certificate and level, a
  Certificates page, a dashboard widget (soonest first) and a host widget.
- The dashboard's Hosts tile counts active hosts no monitoring plugin reports on.

## 0.7.4

### Uptime Kuma
- New built-in plugin over Uptime Kuma's own (socket.io) API. "Sync monitors" plans
  one monitor per service with a link (or, optionally, a port check for every TCP
  service) on the hosts the instance applies to, and changes or removes the ones
  labdex made when services change (they're marked `labdex:<host>/<service>` in their
  description; your own monitors are left alone). It runs after host changes, as a
  plan that goes through approvals. New monitors get the notification ids you list
  and certificate-expiry notifications.
- "Read status" reports each host (up if a monitor of it is up, down if all are
  down) and each service: the Services page and its status dots now prefer a
  monitor over Discovery and say where the status came from. Monitors labdex didn't
  make count for the host their URL or address points at.
- Widgets: all monitors on the dashboard (down first), a host's monitors on its page.
- Needs `python-socketio` (the `uptimekuma` extra; in the Docker image).

## 0.7.3

### Beszel
- New built-in plugin. "Read host status" reports each host whose Beszel system
  (matched by address or name) is up or down, with CPU, memory and disk.
- "Add hosts as systems" plans a Beszel system for every host the instance applies
  to that the hub doesn't know yet (address from a host setting or the primary
  interface). "Install the agent" (a host action) runs Beszel's install script over
  Ansible with the hub key, or a universal token for agents that connect themselves.
- Widgets: every system on the dashboard (down first); CPU, memory, disk, load,
  uptime and agent version on each host.

## 0.7.2

### Host status from monitoring
- Status plugins report what they know about hosts. A host's status: **down if any
  source says down, up if at least one says up**, otherwise what Discovery last saw.
  A report older than "Monitoring reports are fresh for … minutes" (General, 15 by
  default) counts as no data.
- The host list has a status dot and a column per source; the sidebar tree and the
  dashboard use the same status; a host's Plugins tab lists each source with its
  detail and since when.
- When an active host goes from up to down or back, a `host.down` / `host.up`
  notification goes out (Discovery alone never sends one).
- For plugin authors: `ctx.report_status({host: {"state", "detail", "data"}})` and
  `ctx.match_host()`, which maps an address, name, FQDN (any generated DNS name), URL
  or host:port to an inventory host.

### VictoriaMetrics / Prometheus
- New built-in plugin. "Read host status" (every 2 minutes by default) asks the
  status query (`up`), matches each target's `instance` label to a host and reports
  it down when every target of the host is down. Basic or bearer authentication.
- Widgets: "Targets down" on the dashboard; CPU over the last 6 hours (and memory) on
  each host, from node_exporter queries you can change.
- Schedulable actions may set a default interval in the manifest (`schedule_default`).

## 0.7.1

### Widgets
- Plugins can show widgets on the dashboard and on a host's overview. They return
  data in fixed kinds (stat, status, list, table, sparkline) and labdex draws them:
  no plugin code runs in the browser, unknown kinds and fields are dropped and links
  must stay inside labdex or go to http(s). Results are cached per widget (`refresh`)
  and a slow or broken widget shows an error without holding up the page.
- Host widgets appear only where the instance applies to the host, and only to
  people who can see both.
- Built in: Backup → "Last backup" on the dashboard (age, kept, encrypted or not);
  nmap → "Open ports" on each host (documented or not).
- API: `GET /api/widgets`, `GET /api/hosts/<name>/widgets`. Manifest: `widgets:`
  with `id`, `label`, `where` (dashboard, host), `size` (small, wide), `refresh`.

## 0.7.0

### Plugins
- One Plugins section replaces Integrations. The sidebar's "Plugins" page shows every
  plugin with what it brings (actions, plans, exports, hooks, pages, host, endpoints,
  widgets, Ansible: read from its manifest) and its instances, filtered to the ones in
  use. Each plugin has its own page with its instances, actions, hooks and recent jobs;
  an instance (what used to be called an integration) keeps its page and settings.
  `/integrations` redirects; the API keeps its names.
- `GET /api/plugins/<id>`; `/api/plugins` lists each plugin's instances and
  `capabilities`.

### Signed repositories
- Marketplace URLs are replaced by repositories: a signed `index.json` on any web
  server. The repository's key signs the index and every plugin in it. Adding one
  shows its name and key id first; the key is pinned, so a changed or re-signed index
  and plugins signed with another key are refused. Removing a repository stops the
  plugins only it vouched for. Indexes signed with the labdex project key show as
  official.
- `labdex plugin repo SOURCE OUTPUT --key KEY --name NAME` signs every plugin folder,
  zips them and writes the signed index, ready to publish.
- Unsigned marketplace URLs from 0.6 are no longer used; Admin → Plugins lists them.
- API: `/api/plugin-repos` (`/preview`, then `POST` with the key id); `/api/marketplace`
  lists the repositories' plugins; install with `{repository_id, id}`.

## 0.6.4

### Dashboard
- A start page (`/`, "Dashboard" in the sidebar): hosts (up, down, planned),
  networks, services, lint and Discovery at a glance; plans waiting for approval
  (yours first) and approved plans waiting for their window; what's failing (the
  newest run of each action in the last week, until it succeeds again) and what's
  running; the last changes; scheduled templates and change windows coming up; hosts
  per zone; a warning when no worker runs jobs or notifications can't be delivered.
- Everything on it follows your permissions. `GET /api/dashboard`.
- Plugin widgets on the dashboard come with the plugin rework in 0.7.

## 0.6.3

### Ansible Vault and secrets in variables
- Feature and host variables can point at a global secret instead of holding it:
  `admin_password: {$secret: grafana-admin}`. The value is filled in only when labdex
  runs Ansible (in a private temporary folder) and never reaches the inventory, the
  YAML export, the API or History. Secret values are masked as `********` in job logs,
  `--diff` output included.
- Ansible runner: "Ansible Vault password" (and an optional vault id) for files in
  your playbooks that are encrypted with `ansible-vault`.
- File exports: with a vault password, the Ansible inventory export writes secrets as
  `!vault` values (Ansible's own format, 1.1 or 1.2 with a vault id), so a playbook run
  outside labdex decrypts them with the same password. Without one they stay
  references.
- Lint: `secret-missing` when variables point at a secret that doesn't exist. The
  Secrets page lists features and hosts among "used by".
- Secret settings of an integration no longer appear in `labdex_settings`.

### Job templates
- An action with its parameters (and host, for host actions) saved under a name, on the
  integration's page: run it with one click, or on a schedule (every N minutes, or on
  chosen days at a time in a time zone). Scheduled plans go through approvals like
  any other plan.
- Running a template needs the right to apply the integration (and the host); a
  schedule, which runs unattended, needs admin rights on the integration. Jobs show
  the template they came from. API: `/api/job-templates` (+ `/run`).

## 0.6.2

### Notifications
- Channels (Admin → Notifications): ntfy (server, topic, optional token, priority),
  Matrix (homeserver, room id or alias, access token), email (SMTP with STARTTLS/SSL)
  and webhooks (JSON body; with a secret, an `X-Labdex-Signature: sha256=…` HMAC).
  Each picks its events and, optionally, which integrations it hears about.
- Events: `plan.waiting` (needs approval), `plan.applied`, `job.failed`,
  `backup.failed`, `host.down` / `host.up` (for status integrations), `test`.
  Plugins send their own with `ctx.notify(…)`, as `plugin.<id>.<event>`.
- An outbox: messages go out from the worker, and failed ones are retried after 1,
  5, 15 and 60 minutes. "Recently sent" lists them with the error; each channel shows
  its last message and how many failed. A Test button sends one right away and says
  what went wrong.
- New setting "Address of this labdex" (General) for links back to the job.

## 0.6.1

### Approvals and change windows
- Approval policies (Admin → Approvals & windows). A plan is matched, top to bottom,
  by integration name, the zones of the hosts it runs for and how it was started
  (by someone, by a hook, on a schedule). The policy sets the approvals needed
  (0 = applies by itself), who may give them (users and `@groups`; empty = anyone who
  may apply the integration), whether whoever started the plan may approve it, a delay
  after planning, and a change window. Without a match it's as before: you approve
  your own plans.
- Change windows: weekly times in a time zone (e.g. Tue and Thu 20:00–22:00
  Europe/Berlin; a window may run past midnight). An approved plan waits as
  "approved" until its delay has passed and its window is open; the worker applies it.
- A plan the inventory has moved past is planned again when it's approved or its
  window opens. If the new plan has the same changes, the approvals carry over; if
  not, it waits for approval again. A newer plan for the same action keeps the
  approvals of the one it replaces the same way.
- The job page shows the policy, the approvals with comments, why you can't approve
  (not an approver, you started it, already approved), and when an approved plan
  applies. Approved plans can still be discarded, with a comment.
- API: `POST /api/jobs/<id>/approve {comment}` (`/apply` does the same, for older
  clients), `/discard {comment}`; `/api/approval-policies` (with `PUT …/order`) and
  `/api/change-windows`. Approvals are recorded in History.
- `labdex action … --apply` on the command line still applies right away.

## 0.6.0

### Worker and run history
- Jobs are a queue in the database. A dispatcher claims a job, runs it and sends
  heartbeats; a job whose worker stops (no heartbeat for 90 seconds) is marked failed
  instead of hanging in "running".
- New `labdex worker [--slots N]`: runs queued jobs and the scheduler in its own
  process. With `LABDEX_WORKER=external` the web server only queues jobs (Ansible runs
  and scans stay out of the web process, and several gunicorn workers on PostgreSQL
  become possible). The default, `inline`, keeps everything in one container.
  `docker-compose.yml` has a commented `worker` service.
- About shows who runs jobs, the workers seen in the last minute and how many jobs
  wait; a queued job says when no worker is there to pick it up. `GET /api/jobs/queue`.
- Run history per host: a "Runs" tab lists the jobs that ran for the host (host
  actions and Ansible runs that included it), and follows renames.
  `GET /api/jobs?host=NAME`; jobs list their `hosts` and `worker`.

## 0.5.9

### Services
- A service has a description, "listens on" (every address, the primary interface, one
  network, or one address) and, for web links, "link by name or address".
- Discovery ("missing services") and the OPNsense firewall access check only look at
  the addresses a service listens on.
- "Allow from" is picked from zones, areas, networks and aliases
  (`/api/services/sources`); sources labdex doesn't know are marked, and lint warns
  about them (`allow-from-unknown`).
- The Services page is a launcher: cards grouped by zone with an Open button, status
  from Discovery (answering / not answering / not scanned), filters and a table view.

## 0.5.8

### IPv6 discovery
- OPNsense "Import ARP and NDP tables": IPv6 neighbours (`diagnostics/interface/getNdp`)
  with their MACs, next to ARP.
- OPNsense "Import DHCP leases": Kea DHCPv6 address leases too (`kea/leases6/search`);
  delegated-prefix leases (IA_PD) are left out.
- nmap: "Also scan known IPv6 addresses" (on by default). A /64 can't be swept, so it
  scans the IPv6 addresses of the inventory's hosts and those Discovery learned inside
  the chosen networks' IPv6 prefixes, with `nmap -6`, after the IPv4 sweep.
- Link-local, multicast and loopback addresses never enter Discovery.

## 0.5.7

### Prefix delegation
- OPNsense: new action "Read delegated IPv6 prefixes" (schedulable). It reads the
  prefix each WAN interface received (`interfaces/overview/interfaces_info`,
  `ifctl.prefix`) and moves every delegated network prefix to its subnet: prefix ID
  N of size /L inside the delegation.
- A change is recorded in the network's history ("delegated prefix A → B") and
  announced, so host GUA addresses (interface ID × prefix), AAAA records and N6/H6
  aliases follow on the next sync.
- Delegated prefixes that don't fit (prefix ID too large) or whose WAN interface
  reports nothing are listed in the job log and left alone.

## 0.5.6

### IPv6 host addresses
- Each interface has an IPv6 interface ID (`::53`, or `eui64` from the MAC). It is
  used in every IPv6 prefix of the network, so adding a GUA prefix gives every host
  its GUA address at once. An explicit address stays valid and stands for the prefix
  it lies in; a per-prefix override wins over both.
- One AAAA record per prefix; H6 aliases, Discovery, lint (duplicates), Ansible
  (`ip6_all`), Prometheus targets and the firewall access check use every address.
- The host form shows the addresses an interface gets while you type, offers the
  next free interface ID, and has per-prefix overrides under "per prefix…".
- Network usage lists the next free interface IDs of each IPv6 prefix.

## 0.5.5

### Networks: address families
- A network has any number of prefixes, IPv4 and IPv6 (e.g. a ULA and a GUA).
  Each has a mask from a list, an optional gateway and DHCP range, given as a full
  address or a suffix (254 → .254, ::1), and a description.
- IPv6 prefixes can be marked "delegated": from a WAN interface with a prefix ID
  and length. Resolving the live prefix from OPNsense comes in 0.5.7.
- Overlaps inside a network and with other networks are refused.
- Firewall network aliases (N4/N6) hold every prefix of their family; host
  addresses may sit in any prefix; Discovery, nmap targets and the firewall access
  check use all of them.
- The Usage tab shows every prefix: addresses in use, DHCP pool, next free (IPv4).
- The old single-prefix fields stay in the API and mirror the first prefix of each
  family. YAML stays short for simple networks and lists `prefixes` otherwise.

## 0.5.4

### Fixes
- Firewall access check: only inbound rules can grant access. OPNsense lists its
  built-in "let out anything from firewall host itself" with direction `out` and
  no source or destination, which labdex read as any → any. Outbound rules still
  show in the host's rule list, marked "out".

### Networks
- The network page has tabs: Overview (settings, aliases), Usage (addresses in use,
  next free) and Hosts (every host with an interface there, its addresses and MACs,
  and whether its home zone differs).

### Roadmap
- The rest of v0.5 is split into small releases: address families (0.5.5),
  IPv6 host addresses (0.5.6), prefix delegation (0.5.7), IPv6 discovery (0.5.8),
  services (0.5.9). Dashboard, plugins rework, widgets and routing discovery are
  placed in v0.6 to v0.8.

## 0.5.3

### DNS zones
- A network can have a DNS zone of its own: empty = `<host>.<network>.<domain>`
  as before, `@` = `<host>.<domain>`, or e.g. `home.lab` = `<host>.home.lab`.
- A host can have a main DNS zone for its short name, fun names, service names
  and extra records (default: the domain). Address records follow their network.
- Two networks in the same zone give a host distinct names (`<host>-<network>`);
  with `@`, the short name is the address record itself (no CNAME beside it).
- The BIND export writes one `$ORIGIN` block per zone; conflicts are checked by full name.

### Service records
- DNS-SD (`_service._proto` SRV + TXT) is now a checkbox per service, off for new
  services. Services from before 0.5.3 keep theirs.

### Technitium
- Syncs every zone labdex writes into. Each record goes into the closest zone that
  exists on the server; names without a zone there are listed in the job log.
- "Extra zones to read" for import and the host panel (never written). The old
  single "Zone" setting still works and is read like an extra zone.
- Works without permission to list zones (falls back to labdex's own zones).

### Discovery
- MACs: nmap records them when it runs as root on the segment; after every scan the
  scanning machine's ARP table fills in the rest; ARP and Kea leases keep doing it
  too. Each MAC remembers where it came from.
- New "MACs" tab: interfaces in the inventory without a MAC whose address was seen
  with one, filled in with one click (one history entry per host). VIPs are left out.
- Open ports show the address's MAC and vendor.

### Sidebar
- Only the zone tree scrolls, with its scrollbar space reserved: opening and
  closing zones no longer changes the sidebar's width.

## 0.5.2

### Naming
- Zones and Naming have their own sidebar entries (out of Admin); everyone can
  look, administrators change them. Features is always in the sidebar.
- The role part of a hostname (`srv-vm-<role>-01`) is free text. The builder
  suggests defined roles and those already in use; defining one only adds a label.
- The host field "role" is now **function** ("DNS resolver"), to stop the
  clash with the hostname's role. Older YAML files and API clients that send
  `role` still work; Ansible gets `labdex_function` (`labdex_role` stays until 0.7).
- Fun name pools: four neutral built-ins (Greek gods, stars, trees, minerals)
  plus your own (paste a list). A pool can apply to categories, areas or zones;
  "Suggest" on the host form offers unused names from the matching pool.

### DNS
- Each host has a DNS tab: extra records (alias CNAME, A/AAAA, TXT, SRV, MX)
  with placeholders (`{target}`, `{fqdn}`, `{ip4}`, `{ip6}`, `{host}`, `{fun}`),
  and a switch per fun name to publish it or not.
- An alias with an empty value points at the primary record, so it follows
  when the primary interface changes.
- These records are part of the inventory (YAML, exports, Technitium sync).
  The Technitium module's per-host "extra records" moved here on upgrade.

### About
- About page (user menu, Admin → General): version, schema, database, plugins
  with versions and trust, and this changelog. Optional check for new releases
  on GitHub (off by default; Admin → General).

## 0.5.1

### Zones
- A host's home zone now follows its primary interface's network (or the first
  interface in a network). Choose "pin" to keep a zone of your own. When a
  network changes zone, hosts that follow it move along, with a history entry.
- Zone is picked as area + trust level. New hosts no longer default to a zone
  that may not exist.
- Every address shows its network's zone. Someone who may see a zone can now
  view (not edit) hosts with an address there, e.g. the edge firewall for a
  DMZ-only user. The sidebar puts such hosts in the zone they can see.
- Upgrading pins exactly the hosts whose zone differed from their network.

### VIPs
- New host kind "VIP": a shared address with a protocol (CARP, VRRP,
  keepalived, kube-vip, MetalLB, anycast) and member hosts. It gets DNS,
  firewall aliases and services like any host. Members list it on their Network
  tab and get `labdex_vips` plus a `vip_<name>` group in Ansible. The VIP itself
  is never an Ansible target and has no MAC. Hostname type `vip` added.
- Member renames and deletions are followed; YAML import/export keeps members.

### Fixes
- Host pickers (Discovery "add to host…", VIP members) are no longer cut off by
  the table; full names, keyboard selection, flips upwards near the bottom.
- Migrations on SQLite run with foreign keys off, so rebuilding a table can't
  cascade-delete interfaces.

## 0.5.0 — Settle & speed

### Integrations
- OPNsense and Technitium calls checked against the sources of OPNsense 26.1
  (Kea inside OPNsense) and Technitium DNS Server 15.6.2; contract tests pin them.
- OPNsense grid searches POST `rowCount: -1` (all rows) and fall back to GET.
- Firewall rules are recognised as labdex's by the UUID of the
  `automation:labdex` category, which is what OPNsense returns.
- Technitium: the API token goes in `Authorization: Bearer`, not the form body;
  the host panel matches every fun name of a host.

### Speed
- UI query cache (stale-while-revalidate): pages show the last answer at once
  and refresh in the background; every change, from any tab or the CLI,
  arrives on the event stream (`revision`) and refreshes what's on screen.
- List pages stay alive: back from a host keeps filters, search and rows.
- Host links prefetch on hover; saving a host writes the answer into the cache.
- The host picker loads four fields instead of whole hosts.
- Host list renders 200 rows at a time; search no longer matches field names.
- Server: weak ETags with `304 Not Modified` on every GET; a per-revision host
  index behind the host list, sidebar and counts (1000 hosts: host list 45 ms,
  sidebar 60 ms, slim list 5 ms).
- `/api/hosts`: `fields`, `q`, `zone`, `status`, `sort`, `limit`/`offset`
  (`X-Total-Count`); cursor paging on history and jobs (`X-Next-Before`).
- Static files: hashed assets cached for a year, precompressed `.br`/`.gz`.

### Database
- Alembic migrations, run on start; a 0.4 database is adopted automatically.
- PostgreSQL support (optional; SQLite stays the default). The test suite runs
  on both.
- `labdex db current | upgrade | copy --to URL` (move SQLite → PostgreSQL).
- Backups carry a portable `database.json` and restore into either database.

### Project
- GitHub Actions: tests on SQLite and PostgreSQL, browser tests (Playwright),
  a performance budget on 1000 generated hosts, Docker build; multi-arch images
  to GHCR on tags.
- `tools/big_inventory.py` makes large test inventories.
- Fixed: a hook-triggered re-run raised an error after starting the job.

## 0.4.0

Editable zones, discovery workflow (adopt, assign, paging, Kea leases and DNS
records as sources), Server-Sent Events, sidebar with zone tree and module
menu, plugin store with signatures and trust levels, host page tabs, dotted
service DNS names, per-interface DNS, admin panel, Kea reservations, firewall
aliases by id in `automation:labdex`, hostname builder, multiple fun names.

## 0.3.0

Module framework with selectors and per-host settings, Technitium DNS,
OPNsense aliases and access checks, Ansible runner with features, secrets
vault, backups (folder and S3).

## 0.2.0

Users, groups, roles and scopes, change history with revert, plugins with
plan/apply, nmap discovery.
