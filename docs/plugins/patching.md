# Patching (Ansible)

The **Patching** integration installs the waiting updates on the hosts of a site with Ansible,
reboots where one is needed if you allow it, and reports every host's state back to the
[Patches](../guide/patches.md) page. Nothing reaches a host before a person approved the run.

Add it under **Plugins › Patching (Ansible) › Add**, in the site it should patch. Like the Pulumi
plugin it lives in `plugins/patching/` of the labdex repository for now.

## What it needs

- Ansible in labdex: build the image with `docker build --build-arg ANSIBLE=1 .`. *Check setup*
  says what's missing.
- SSH to the hosts: an SSH user and private key (share one key with the Ansible runner by
  picking the same global secret), and sudo for that user.
- Python 3 on the hosts, which Debian, Ubuntu, Proxmox VE and RHEL-like systems have.

## Install updates

*Install updates* is a plan:

1. **Plan.** Each host the integration applies to that has updates waiting, according to its
   last patch report, gets a line: how many updates, how many of them security updates, and what
   happens about a reboot. Planning doesn't connect to any host. Hosts without a recent report are
   left out (so switches and appliances are never touched) unless you name them or turn on
   *Patch hosts without a report*.
2. **Approve.** Every run waits for a person's approval, even when an
   [approval policy](../guide/plans.md) would let it apply by itself. A policy can ask for more
   (two approvals, certain approvers, a change window), not for less.
3. **Apply.** One play, one host at a time by default (*Hosts at a time*). It upgrades with the
   host's package manager (`apt dist-upgrade`, `dnf`/`yum upgrade`), checks whether a reboot is
   needed (`/var/run/reboot-required`, `needs-restarting -r`), reboots if allowed, and runs
   [`labdex-patch-report`](../guide/patches.md#labdex-patch-report) on the host to report back.
   The run stops at the first host that fails; the hosts after it stay as they were. Whatever
   was reported before the failure still counts.

| Parameter | |
| --- | --- |
| Only these hosts | host names; empty = every host the integration applies to |
| Reboot | `never` or `if_needed`; empty = the integration's setting |
| Only hosts with security updates | leave out hosts that only have other updates |

### Reboots

*Reboot* is `never` by default. With `if_needed`, a host is rebooted after patching when it asks
for it, except:

- a host that runs guests in the inventory (a Proxmox node with VMs, a Docker host), unless
  *Reboot hosts that run guests* is on;
- a host whose *Never reboot this host* setting is on.

The plan says for each host whether it would be rebooted and why not.

### On a schedule

Save *Install updates* as a [job template](../operate/templates.md), for example every Sunday at
03:00 with *Reboot: if_needed*. The plan is made on time and waits for an approval (the
`plan.waiting` notification tells you); with a change window in the policy it then applies in
the window, not the moment you approve.

## Read patch state

*Read patch state* runs `labdex-patch-report --dry-run` on every host over SSH and shows the
result on the Patches page, without installing anything there and without root. It's an
alternative to the cron job on each host, and can run on a schedule (*Schedule* in the settings).

## Leaving hosts out

Use the integration's *Applies to* selectors (for example `zone:DC_*`), or a host's
*Never patch this host* setting.
