# Working fast

## Dashboards

The start page is the shared **Overview** dashboard: cards on a grid. *Edit* (for
administrators on shared dashboards, for you on your own) lets you drag a card by its
handle, resize it from the corner, remove it, and add cards: the numbers, approvals,
failing and running jobs, the last changes, what's coming up, hosts per zone, every
plugin widget or just one, **links** of your own, the inventory's **services** with their
status (all, or by tag, zone or category), and notes. *Done* saves; *Cancel* forgets.
With a card's handle focused, the arrow keys move it and Shift + arrows resize it.

**+ Dashboard** makes another one: a page of links (a start page like Homarr's, built
in), a wall for the rack room. Yours are only yours; administrators can make shared
ones. Links open http(s) addresses or labdex pages, nothing else.

## Search and go

<kbd>Ctrl</kbd> <kbd>K</kbd> (<kbd>⌘</kbd> <kbd>K</kbd> on a Mac), or <kbd>/</kbd> anywhere
outside a text field, opens one search box for everything:

- **Hosts** by name, fun name, function, tag, address or MAC. A MAC can be typed any
  way: `AA-BB-CC-11-22-33`, `aabb.cc11.2233` or just `aa:bb:cc`.
- **Networks** by name, by VLAN (`vlan 100`), or by an address inside them: type
  `10.10.100.53` and the network it belongs to shows up too.
- **Services**, **aliases** (by name or firewall id) and **integrations**.
- **Actions** of integrations you may run, like *Check drift* or *Sync aliases*: Enter
  starts it and opens the job.
- **Pages** and **New host / New VIP / New network**.

- **Tags**: `tag:backup` (or just a word that is a tag) offers the hosts list filtered
  to it.

<kbd>↑</kbd> <kbd>↓</kbd> choose, <kbd>Enter</kbd> opens, <kbd>Esc</kbd> closes. You
only find what you're allowed to see.

## Physical, virtual, tags

The Hosts page has three tabs: **Physical** (machines and appliances), **Virtual**
(anything that runs on another host, plus VMs and containers by their name) and
**VIPs**. *Host* on the Virtual tab starts a guest.

The search box on the hosts list takes plain words and terms, combined:

| Term | Matches |
| --- | --- |
| `tag:backup` | hosts tagged backup; several `tag:` terms must all match |
| `zone:dmz`, `status:offline`, `cat:srv` | zone, status, category |
| `on:srv-pm-pve-01` | guests of that host |
| `net:srv` | hosts with an address in that network |

Values with spaces go in quotes: `tag:"off site"`. A tag chip in the list adds its term.
**Group by** (tag, zone, runs on, category or status) puts a heading over each group;
a host with two tags shows under both. *Only these* narrows the list to one group.

**Saved searches.** When the search or a filter is set, the *Saved* menu offers *Save this
search …*: it keeps the search text, filters, tab and grouping under a name, and picking it
later sets them all again. Searches are yours; an administrator can share one with everyone.

## Many hosts at once

On the hosts list, tick the hosts (<kbd>Shift</kbd>-click ticks a range; the box in
the header ticks every host the filters show). The bar that appears offers:

- **Change**: status, runs on, tags and features to add or remove, a zone (pinned, or
  following the network again), and an integration turned on or off for these hosts.
  Only what you set changes.
- **Delete**, after a confirmation.

A change applies to all of them or to none: if one host doesn't accept it, nothing
changes and the dialog says which host and why. Each host gets its own history entry
(so each can be reverted), all with the same summary.

## Keeping your place

- Back from a host returns to the list where you left it: same scroll position,
  filters and search. Lists show what they had at once and refresh in the background.
- <kbd>Ctrl</kbd> <kbd>S</kbd> (<kbd>⌘</kbd> <kbd>S</kbd>) saves the host or network
  you're editing.
- Leaving a host or network with unsaved changes asks first, also when closing the tab.
- What you type in a host or network form is kept as a draft in this browser (for two
  weeks). Come back and the page offers it: **Restore them** or **Discard**. Nothing is
  applied until you press Save, and saving clears the draft.

## The topology map

Two views: **Physical** (hypervisors as boxes with their guests, cables between ports)
and **Networks (L3 / VLANs)** (every network as a node, each host joined to the
networks it has an address in; hosts in several networks, like routers and firewalls,
are diamonds). Hosts show their icon. Cables are coloured by speed (1G blue, 2.5–5G
cyan, 10G green, 25G amber, 40G and more red; grey below 1G or without a speed) and
drawn thicker when faster; the legend shows the speeds on the map.

**Drawing a cable**: *Cable*, then click both ends (a host, or a port inside a
hypervisor's box). The dialog offers each host's interfaces; an unnamed one can be
named there, and *New port…* adds an interface to that host. The cable is saved on both
hosts, with history. In the side panel of a host, the bin next to a cable removes it.

Everybody sees the same map, per view. People who may edit hosts arrange it: a node
dragged somewhere stays there for everyone, and the hint says who moved it last.
*Arrange again* starts over (after asking). People who only view can drag nodes for a
moment, but the map goes back when they reload.

## The sidebar

One section is open at a time: going to a page opens its section and closes the
others, and a page under a list (a host) marks the list (Hosts). *Collapse* at the
bottom, or <kbd>Ctrl</kbd> <kbd>B</kbd>, shrinks the sidebar to icons and back. Pages
that plugins add live as tabs on their integration (Automation → Plugins → the
integration), not in the sidebar.

## Light or dark

The menu under your name has **Theme**: Auto (follows your system), Light or Dark. It's
kept in this browser. The search box knows it too: type `light` or `dark`.

## Keyboard and phone

- Every list row that opens something can be reached with <kbd>Tab</kbd> and opened with
  <kbd>Enter</kbd>; "Skip to the page" (the first <kbd>Tab</kbd>) jumps past the sidebar.
- In a dialog, <kbd>Tab</kbd> stays inside it and <kbd>Esc</kbd> closes it.
- On a phone the sidebar is behind the menu button and the search button sits next to
  it; the hosts list keeps name, function, zone and status, and no page scrolls sideways.
