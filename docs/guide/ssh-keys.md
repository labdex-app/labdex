# SSH keys through the agent

labdex can keep the list of who may log in where over SSH, put it on your hosts through their
agents, and show where a host differs. It works with ordinary SSH public keys: labdex never needs
a private key, and it doesn't run a certificate authority for your logins (see
[Design: SSH certificates](../develop/design/ssh-certificates.md) for the later step).

It needs the **Go agent**. The Python agent doesn't do it.

## How it works

- labdex **never edits** a user's own `~/.ssh/authorized_keys`. The agent writes root-owned files
  instead: `/etc/ssh/labdex/<user>.keys` (labdex's keys for that user) and
  `/etc/ssh/labdex/<user>.break-glass` (the host's emergency key). An sshd drop-in adds them to
  `AuthorizedKeysFile`, so sshd reads them as well as the users' own files.
- Only the users that the host's `agent.conf` names in `ssh_users` are managed. Root only if you
  list it.
- Writing needs the `ssh_keys` capability **at both ends**: in `agent.conf` (`install.sh --ssh-keys`)
  and switched on for the agent in labdex by an administrator. Without it labdex only reports.
- **Plan, then apply.** *Agents › SSH keys* compares what labdex wants with the agent's last
  report and shows each host's state. An administrator applies per host; the apply sends the whole
  wanted list and the agent replaces labdex's files in one step.

## Set up a host

Install (or reinstall) the Go agent with the users labdex may manage:

```sh
sudo sh install.sh --url https://lantern.example.net --token <one-time token> \
  --ssh-keys --ssh-users "admin deploy"
```

This adds `ssh_keys` to `allow` and `ssh_users = admin deploy` to `/etc/labdex-agent/agent.conf`,
makes `/etc/ssh/labdex`, writes `/etc/ssh/sshd_config.d/05-labdex-keys.conf`, checks it with
`sshd -t` (and puts the old state back if that fails) and reloads sshd.

`--ssh-keys-only` makes sshd read **only** labdex's files, not `~/.ssh/authorized_keys`: then labdex
decides who logs in. Set and apply a break-glass key first, and check that you can log in with it.

Then, in labdex, an administrator switches on `ssh_keys` on the host's **Agent** tab.

## Keys and where they go

On **Agents › SSH keys**:

1. **Key** adds a person's public key (one line of their `.pub` file). labdex accepts Ed25519,
   ECDSA, security keys (`sk-…`) and RSA with 3072 bits or more, without options in front
   (`command=`, `from=`): a key in labdex can't do more than the list shows.
2. **Grant** says as which user it may log in: on one host, or on every host that manages that user.
3. Each host shows its state:

   | State | Means |
   | --- | --- |
   | In sync | the host has exactly labdex's keys and its break-glass key |
   | Differs | an apply would add or remove keys (or the break-glass key isn't there yet) |
   | No break-glass key | labdex has none for this host |
   | Other keys too | users' own `authorized_keys` hold keys labdex doesn't manage |
   | Report only | `agent.conf` names no `ssh_users` |

   Click a host for its plan: what an apply adds and removes per user, the keys in the users' own
   files (by fingerprint, named when labdex knows the key), and what sshd reads.
4. **Apply** puts the keys on the host. Removing a key or a grant takes it off with the next apply.

The agent reports again on the discovery schedule, and after each apply.

## Break-glass key

Every host should have one key that works when labdex or the agent doesn't. **Break-glass** on a
host's row either:

- makes a new Ed25519 key pair: labdex shows the private key **once** (copy or download it into a
  password manager, offline) and keeps only the public half; or
- takes a public key you made offline, so the private key never touches labdex.

An apply never removes a host's break-glass key. It can only replace it with a new one.

## What labdex can and can't do here

- It writes only `/etc/ssh/labdex` and only for the users in `ssh_users`; the root side checks
  `agent.conf` again, whatever the job says.
- A host editor can see a host's key state and ask for a report. Keys, grants, break-glass keys
  and applies are for administrators, and each is in the history.
- With `--ssh-keys-only`, whoever is an administrator in labdex decides who logs in to that host.
  Keep the break-glass key offline.
- Uninstalling the agent leaves the drop-in and `/etc/ssh/labdex` in place, so nobody is locked
  out; remove both yourself once nobody needs them.
