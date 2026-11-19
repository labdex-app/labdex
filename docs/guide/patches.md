# Patches

The **Updates** page (Operate › Updates) shows, for every host, which updates are waiting, how many of them are security
updates, whether it needs a reboot, and when it was last patched. A host's dashboard has the same
in its *Patches* card.

Something reports to labdex:

- the [host agent](agent.md), which reports on a schedule and on demand, and installs updates once
  you switch that on;
- **labdex-patch-report** on the host itself (Debian, Ubuntu, Proxmox VE, Fedora, RHEL and friends),
  from cron, a systemd timer or Ansible;
- the [Patching](../plugins/patching.md) plugin, which runs the same report over SSH, and installs
  the updates after an approval;
- **a plugin**, for the systems it already talks to.

Each source keeps its own report per host. When several report the same host, the most recent report
counts, for the updates and for the reboot. A report older than three days is shown as
*stale*, not as up to date.

| State | |
| --- | --- |
| security | security updates are waiting |
| reboot | the host needs a reboot (and has no security updates waiting) |
| updates | updates are waiting |
| up to date | nothing is waiting |
| stale | the newest report is more than three days old |
| error | the source couldn't read the package manager |
| no report | nothing reports this host |

When a host gets security updates or needs a reboot, labdex sends the `host.patches` notification
to the channels that subscribed to it.

## labdex-patch-report

A single Python 3 file with only the standard library, so it runs on a bare VM. Download it from
your labdex (the link is on the Updates page) and give it an [API token](rbac.md) of a user who may
edit the host:

```console
# curl -fsSL -H "Authorization: Bearer $LABDEX_TOKEN" https://labdex.example.net/api/patches/agent \
    -o /usr/local/bin/labdex-patch-report && chmod +x /usr/local/bin/labdex-patch-report
# install -d -m 700 /etc/labdex && install -m 600 /dev/stdin /etc/labdex/token <<< "$LABDEX_TOKEN"
# labdex-patch-report --dry-run
```

It reads what the package manager already knows (`apt-get -s dist-upgrade`, `dnf check-update`,
`/var/run/reboot-required`, `needs-restarting -r`; on Proxmox VE also whether a newer kernel is
installed than the one running) and posts it to `/api/patches` under the host's short name.

```cron
# /etc/cron.d/labdex-patch-report
17 */6 * * * root /usr/local/bin/labdex-patch-report --url https://labdex.example.net --token-file /etc/labdex/token --refresh
```

| Option | |
| --- | --- |
| `--url URL` | labdex's address (or `LABDEX_URL`) |
| `--token-file FILE` | a file with the API token (or `LABDEX_TOKEN`) |
| `--host NAME` | the host's name in labdex, if it isn't the short hostname |
| `--source NAME` | name the source (default `agent`) |
| `--refresh` | update the package lists first (`apt-get update`, needs root) |
| `--ca-file FILE` | a CA bundle, for a labdex with a private certificate |
| `--dry-run` | print the report and send nothing |

With Ansible, run it from a play on the hosts of a selector, or install the cron file with it.

## The API

`POST /api/patches` takes one host or several. Every field but the host is optional; `pending` and
`security` are counted from `packages` when left out.

```json
{"host": "srv-vm-dns-01", "os": "Ubuntu 24.04.1 LTS", "kernel": "6.8.0-45-generic", "reboot": true,
 "patched_at": "2026-11-10T06:00:00Z",
 "packages": [{"name": "openssl", "from": "3.0.13-0ubuntu3.4", "to": "3.0.13-0ubuntu3.5", "security": true}]}
```

```json
{"source": "ansible", "hosts": {"srv-vm-dns-01": {"pending": 2, "security": 1}, "srv-vm-mon-01": {"pending": 0}}}
```

It needs the right to edit each host. Hosts labdex doesn't know are skipped and listed in the answer.
`GET /api/patches` (`?site=`, `?state=security`) lists every host with its state and the counts, and
`GET /api/hosts/<name>/patches` gives one host's sources with their packages.

## In a plugin

A plugin reports for the hosts of its site with `ctx.report_patches({host: {...}})`, with the same
fields as the API. By default the report replaces what the plugin said before, so a host it no longer
lists loses its row; pass `replace=False` to add to it.
