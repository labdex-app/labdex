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

### Kubernetes nodes

When some of the hosts are nodes of a Kubernetes cluster, fill in *Kubernetes nodes* in the
settings (API server, a bearer token, the cluster CA). A host counts as a node when the node has its
name or one of its IP addresses. Then:

- the plan says so for each node (*drained first, uncordoned once Ready*); planning only reads
  the node list;
- the other hosts are patched first, in one play; then the nodes, **one at a time**: cordon,
  evict its pods (DaemonSet pods, static pods and finished pods stay), patch and reboot it as
  above, wait until it is `Ready` again, uncordon;
- a pod a PodDisruptionBudget holds back is tried again until *Wait for a drain* runs out. Then
  the run stops, and the node **stays cordoned** with nothing installed, so you can look at it.
  The same goes for a node that isn't `Ready` within the reboot timeout. Uncordon it yourself
  (`kubectl uncordon <node>`) once it's fine.

Give the token its own service account, with only what draining needs:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: labdex-patching
rules:
  - apiGroups: [""]
    resources: [nodes]
    verbs: [get, list, patch]
  - apiGroups: [""]
    resources: [pods]
    verbs: [list]
  - apiGroups: [""]
    resources: [pods/eviction]
    verbs: [create]
```

### On a schedule

Save *Install updates* as a [job template](../operate/templates.md), for example every Sunday at
03:00 with *Reboot: if_needed*. The plan is made on time and waits for an approval (the
`plan.waiting` notification tells you); with a change window in the policy it then applies in
the window, not the moment you approve.

## Read patch state

*Read patch state* runs `labdex-patch-report --dry-run` on every host over SSH and shows the
result on the Updates page, without installing anything there and without root. It's an
alternative to the cron job on each host, and can run on a schedule (*Schedule* in the settings).

## Leaving hosts out

Use the integration's *Applies to* selectors (for example `zone:DC_*`), or a host's
*Never patch this host* setting.
