# Design: Ansible through the agent

Status: **decided**, being built (see [Decisions](#decisions)). The pipe in the protocol and the Go
agent is the first part: [Ansible over the link](../agent-protocol.md#ansible-over-the-link). labdex's side (SSH CA, certificates, the broker, the runner) is the second:
[Ansible through the agent](../../guide/agent.md#ansible-through-the-agent). The Python agent stays as it is; the new parts are for the Go agent only.

## Goal

labdex runs Ansible against a host **through the agent's link** instead of over the network:

- the host needs no open SSH port and no key, password or vault secret of labdex's at rest;
- labdex holds every credential and hands out only short-lived ones, per run and per host;
- installing the agent also installs what the host's Ansible modules need, driven by the features
  (playbooks and roles) the host has.

## What we have today

Two ways to run Ansible, both with gaps for this goal:

| | Ansible runner (plugin `ansible`) | `ansible.run` agent job |
| --- | --- | --- |
| Where Ansible runs | in labdex (the controller) | on the host, `-c local` |
| Transport | SSH from labdex to the host's port 22 | none: the playbook bundle comes over the link |
| Credentials | one SSH private key in the integration settings, written to a temp file per run | none for transport; `vars` land in a file on the host while the job runs |
| Vault secrets | revealed in labdex, masked in logs | would have to travel to the host |
| Host needs | sshd reachable from labdex, Python, module libraries | ansible-core, collections, Python, module libraries |
| Privileges | whatever the SSH user may do | the agent must run as root |

The runner already does most of what we want (vault, masking, check/diff, features); it lacks a
transport that doesn't need a reachable sshd and a long-lived key.

## 1. Transport: SSH inside the agent's link

Recommended. Ansible keeps running in labdex with its normal `ssh` connection. Only the path
changes: `ssh` talks to the host's sshd through a byte pipe that the agent opens to
`127.0.0.1:22` on its own host.

```text
labdex                                              host
ansible-playbook                                     sshd (127.0.0.1:22, or any)
  └─ ssh -o ProxyCommand="labdex agent-pipe %h"         ▲
        └─ unix socket ─► link thread ═══ WebSocket (mTLS) ═══► agent ─► TCP 127.0.0.1:22
```

1. The runner asks for a **pipe session** for each host in the run: a signed job
   `ansible.session` with the run id, the host and an expiry (the job's timeout, at most 60 min).
   The agent accepts it only with the `ansible` capability on both ends, like every write job.
2. `ssh` starts `labdex agent-pipe <host>` as its `ProxyCommand`. The helper connects to a Unix
   socket of the labdex process (`<data>/run/agent-pipe.sock`, 0600) with the run's one-time token,
   and the link thread of that host's agent carries the bytes.
3. The agent opens TCP to `127.0.0.1:<ssh_port>`. The port comes from `agent.conf` (default 22),
   **never from the job**, so the pipe can't reach anything else on the host or its network.
4. New link messages, both ways: `pipe.open {id, session}`, `pipe.data {id, data}` (base64),
   `pipe.close {id, error}`. At most 4 pipes per session (Ansible opens one per worker, and
   `ControlPersist` reuses them). The agent closes every pipe when the session expires, the job is
   cancelled, the link drops or the agent is revoked.

The host's sshd can then listen on `127.0.0.1` only, and port 22 can be closed in the firewall.

Why not an own Ansible connection plugin that sends each command over the link? Ansible hands a
connection plugin shell command lines (`/bin/sh -c '… python3 …'`), temp-folder handling and file
copies. We would have to write and keep up a connection plugin, teach the agent to run arbitrary
shell lines (which it deliberately never does) and re-implement `become`. SSH already does all of
that, is reviewed far more than anything we would write, and keeps the agent a dumb pipe.

`ansible.run` (local, root agent) stays for now; see decision D5.

## 2. Credentials: where they live, scope, rotation

### What labdex holds

| Secret | Where | Used for |
| --- | --- | --- |
| Job signing key (exists) | `<data>/agent-signing.key` | signing `ansible.session` and every other job |
| Agent CA (exists) | `<data>/agent-ca.*` | the link's mTLS |
| **SSH user CA** (new, Ed25519) | in the vault (D3) | signing per-run SSH certificates |
| Vault entries (exist) | the vault | `{$secret: …}` in feature and host variables |
| ansible-vault password (exists) | integration setting | files encrypted with `ansible-vault` |

### What the host holds

Only public material: the SSH CA's public key (`/etc/ssh/labdex-ansible-ca.pub`), the allowed
principal for this host, and the agent's own key and certificate (as today). Nothing that opens
another host or reveals a vault entry.

### Per run, per host

For each host in a run labdex makes a fresh Ed25519 key in the run's temp folder (0600, removed
when the run ends) and signs an SSH certificate with:

- **principal** `labdex-ansible@<host>`: each host accepts only its own name
  (`AuthorizedPrincipalsFile`), so a certificate for one host opens no other;
- **valid** from 1 min before the run to the job's timeout (at most 60 min);
- **key id** `<run id>/<host>/<user who started it>`, so sshd's log names the run;
- **`source-address=127.0.0.1,::1`**: sshd takes it only from the host itself, which means only
  through the agent's pipe. A leaked certificate is useless from the network;
- no port, agent or X11 forwarding and no PTY.

The host key is pinned, not trusted on first use: the agent reports sshd's public host keys in its
facts (over the authenticated link) and labdex writes them into the run's `known_hosts` with
`StrictHostKeyChecking=yes`.

### Vault secrets

As today in the runner: labdex resolves `{$secret: …}` when the run starts, passes them as extra
vars, masks them in the log and decrypts `ansible-vault` files on the controller. With pipelining on
(required for this connection), a module's arguments reach the host on Python's stdin and are not
written to disk by Ansible. A secret a playbook **deploys** (a config file, an API token for a
service) does end up on the host: that is the point of the task, and `no_log: true` keeps it out of
the log.

### Rotation and revocation

- **Run keys and certificates** never need rotation: they live for one run.
- **SSH CA**: `labdex ansible rotate-ca` makes a new CA. Hosts trust both public keys for a
  grace period (`TrustedUserCAKeys` takes several), pushed by a run signed with the old one, or by
  running `install.sh` again; then the old one is dropped. labdex warns when the CA is older
  than a year.
- **Revoking an agent** closes its link at once, and with it every pipe: Ansible can't reach that
  host any more even with a valid certificate, because sshd takes it only from `127.0.0.1`.
- Hosts that had the old CA and are offline during rotation show as *needs re-trust* on the
  Agents page.

## 3. What the host needs, per module

Over SSH the host needs **no ansible-core and no collections**; the controller sends each module.
It needs:

1. **Always**: Python 3 (a version the image's ansible-core supports for targets), `sudo` for
   become, and an sshd. `install.sh` installs what is missing from the distribution (it already
   does this for Python and sudo).
2. **Per module**: the libraries or programs the module imports or runs on the target. Examples:

   | Module | Debian/Ubuntu | RHEL family |
   | --- | --- | --- |
   | `ansible.builtin.apt`, `apt_repository` | `python3-apt` | — |
   | `ansible.builtin.dnf` | — | `python3-dnf` |
   | `ansible.builtin.git` | `git` | `git` |
   | `ansible.builtin.unarchive` | `unzip`, `tar` | `unzip`, `tar` |
   | `community.docker.*` | `python3-docker`, `python3-requests` | `python3-docker`, `python3-requests` |
   | `community.postgresql.*` | `python3-psycopg2` | `python3-psycopg2` |
   | `community.mysql.*` | `python3-pymysql` | `python3-PyMySQL` |
   | `ansible.posix.firewalld` | `python3-firewall` | `python3-firewall` |
   | `ansible.posix.acl` | `acl` | `acl` |
   | `community.crypto.openssl_*` | `python3-cryptography` | `python3-cryptography` |

   The table lives in the repository as data (`backend/labdex/ansible_requirements.yaml`), so
   adding a module is a one-line change, and a plugin can ship lines for its own collection.

### How the install follows the modules

1. **Find the modules.** labdex walks every playbook and role of the host's features (the same
   files the runner would run, at the site's commit) and collects the task modules, with short
   names resolved to their FQCN. A task it can't see through (`include_tasks: "{{ x }}.yml"`) is
   listed as *unknown*; a feature can add `requires: [community.docker.docker_container]` by hand.
2. **Map to packages** with the table and the host's OS family (from the agent's facts). A module
   with no entry shows as *unknown requirement* instead of being guessed.
3. **At agent install**: the Agent tab's install command carries the list:
   `install.sh … --ansible --packages "python3-docker python3-psycopg2"`. The script installs them
   from the distribution like the base packages.
4. **Afterwards**: features change, so before every Ansible apply labdex runs a short
   **prepare** play on the host (`ansible.builtin.package` with the current list, `state: present`).
   It shows in the plan (check mode) like any other change, and it never removes packages.
   Hosts without become can't install; the Agent tab lists what is missing for an administrator
   to install by hand.
5. The Agent tab shows the module list and per package *installed / missing*. The agent reports
   which of the listed packages are installed in its facts (new; it asks the package manager the
   way the patch report does).

Only distribution packages (D4). Ubuntu first (D6); the RHEL column waits until another distribution is wanted.

## 4. Privileges

Two users, so the agent itself never holds what Ansible needs:

| User | Logs in | sudo |
| --- | --- | --- |
| `labdex-agent` (exists) | never | the fixed lines in `labdex-agent sudoers`, unchanged |
| `labdex-ansible` (new) | SSH only, certificate only, from `127.0.0.1` only | none, or root with **become** (opt-in) |

`install.sh --ansible` creates `labdex-ansible` (no password, shell `/bin/sh`, home
`/var/lib/labdex-ansible`) and writes `/etc/ssh/sshd_config.d/labdex-ansible.conf`:

```text
TrustedUserCAKeys /etc/ssh/labdex-ansible-ca.pub
Match User labdex-ansible
    AuthorizedPrincipalsFile /etc/ssh/labdex-ansible-principals
    AuthenticationMethods publickey
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
```

and the principals file, whose `from=` option refuses the user from anywhere but the host itself
(the certificate's `source-address` says the same, so either one alone would do):

```text
from="127.0.0.1,::1" labdex-ansible@srv-vm-dns-01
```

It checks the result with `sshd -t` before reloading, the way it checks sudoers with `visudo`.

### Become, and why an allowlist of exact commands doesn't work here

The agent's own sudo rules list exact command lines. That can't be done for Ansible: `become` runs
`sudo -u root /bin/sh -c 'echo BECOME-SUCCESS-<random>; /usr/bin/python3'` and feeds the module
on stdin. The random marker changes every task, and Python reading code from stdin can do anything.
Any sudo rule that lets Ansible become root is, in effect, full root. We should say so rather than
ship a rule that only looks narrow.

So **root is opt-in per host, at both ends**, like the root agent today:

- on the host: `install.sh --ansible-become` writes
  `labdex-ansible ALL=(root) NOPASSWD: ALL` to `/etc/sudoers.d/labdex-ansible` (checked with
  `visudo`). Without it `labdex-ansible` has no sudo, and tasks with `become: true` fail;
- in labdex: an administrator ticks *Ansible may become root* on the host's Agent tab.

The real limits are on the labdex side, where they can be enforced:

- a run needs a signed `ansible.session` job; nothing else opens a pipe;
- certificates are per host, short-lived and only valid from `127.0.0.1`;
- runs start only from a merged commit of the site's repository (the production rule: every apply
  goes through a reviewed merge request);
- a new permission `ansible:apply`, scoped like other rights (site, zone, host);
- an optional **module allowlist** per host or feature (for example no `command`, `shell`, `raw`,
  `script`). It's checked on the playbooks before the run, so it is a guard rail against mistakes,
  not a security boundary: a dynamic include can get past it;
- an audit entry on both ends: labdex (who, which commit, which hosts) and the agent
  (`pipe.open`, `pipe.close`, bytes each way), plus sshd's log with the certificate's key id.

## 5. Risks

**labdex holds every key.** Whoever controls labdex (or its data folder) can sign jobs, mint SSH
certificates and read the vault, so they are root on every host with become. That is the price of
"nothing on the host". What we do about it:

- **Harden the labdex host**: no other services on it, not reachable from the internet (VPN or a
  reverse proxy with SSO in front), administrators with a second factor, updates applied, disk
  encrypted, outgoing traffic limited to what labdex needs.
- **Keep the SSH CA key in the vault** (D3), so a copy of the data folder without the vault
  passphrase can't mint certificates. Runs then need an unsealed vault.
- **Short certificates and per-host principals** limit what a leaked certificate does to one host,
  through its agent, for under an hour.
- **Back up** the data folder with labdex's encrypted backups, and keep `secret.key` and the vault
  passphrase apart from them. Restore it once on a spare VM to check it works. Losing the SSH CA
  is recoverable (new CA, re-trust); losing the job signing key means enrolling every agent again.
- **Keep a way in without labdex**: a console or an SSH key of your own that doesn't depend on
  labdex, for the day labdex is down or restored from an old backup.
- **Availability**: if labdex or its link is down, no Ansible runs. That is accepted for a homelab
  inventory; the runner's plain SSH mode stays for hosts without an agent.

Other risks:

- **A link thread per pipe session**: the Ansible traffic shares the WebSocket with heartbeats and
  jobs. Base64 adds a third. Large file copies (`copy` of a big file) will be slow; we cap a
  session's bytes and say so. Binary WebSocket frames can come later.
- **The separate `labdex worker` process** has no links (and no vault). Pipes work only in the
  labdex process, as vault secrets already do; the worker refuses such runs with that reason.
- **sshd configuration** on hosts we don't own: the drop-in can conflict with an existing
  `Match` block. `sshd -t` and a test login over the pipe at install catch it before it matters.

## Agent tab

To be built with the agent UI rework (separate thread), on the host's Agent tab:

- an **Ansible** section: off / on / on with become, for both ends, with what's missing;
- the install command with `--ansible`, `--ansible-become` and `--packages` filled in. The
  token call builds it (`agent_token` in `api/agents_api.py`); when it rerolls an existing agent's
  key, these flags are kept the way `--allow` and `--root` are;
- sshd's host key fingerprint as the agent reported it, and whether the host trusts the current CA;
- the modules this host's features use, their packages, *installed / missing / unknown*;
- the last runs, with commit, who, result and bytes.

## Plan, after the decisions

Each step a draft pull request of its own:

1. **Protocol and Go agent**: `ansible.session`, the `pipe.*` messages and `ssh_port` in
   [agent-protocol.md](../agent-protocol.md); the pipe (to `127.0.0.1:<ssh_port>` only) and sshd's
   host keys in the facts, with Go tests.
2. (merged into 1)
3. **labdex**: SSH CA, per-run certificates, the pipe broker and `labdex agent-pipe`, the runner
   choosing the agent pipe per host (hosts without one keep plain SSH).
4. **install.sh**: `--ansible`, `--ansible-become`, `--packages`; the user, sshd drop-in, sudoers.
5. **Requirements**: the module table, the module scan, the prepare play.
6. **Agent tab** (with the UI thread).
7. **Docs** and a full rehearsal: a real playbook through the pipe in an Ubuntu container.

## Decisions

Decided by Mike on 2026-11-24:

| | Question | Decision |
| --- | --- | --- |
| D1 | Transport | SSH inside the agent pipe |
| D2 | Become | Root opt-in per host, at both ends, knowing it is full root |
| D3 | SSH CA key | In the vault: Ansible runs need the vault unsealed |
| D4 | Module requirements | Distribution packages only |
| D5 | The existing `ansible.run` | Remove it once the pipe has run on real hosts |
| D6 | First distribution | Ubuntu only; other distributions later |
