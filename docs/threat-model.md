# Threat model

The ways into labdex and onto the hosts it manages, what guards each one, and what is left.
Update this page whenever a pull request adds a way in (an API, an agent message, a plugin hook,
a webhook).

TODO: the web UI and REST API, sign-in, plugins and webhooks are not written up here yet. Until
they are, see [Security review (1.0)](operate/security-review.md) and [Security](operate/security.md).

## Host agent link

| | |
| --- | --- |
| Way in | The agent dials out to labdex over a WebSocket on TLS (`/api/agents/link`); the host opens no port. |
| Who can use it | An agent with a key labdex issued at enrolment (stored as a hash), and, when `LABDEX_AGENT_MTLS=required`, a client certificate from labdex's agent CA. |
| What it can do | Run jobs from a fixed list, each signed by labdex's job-signing key and checked by the agent (signature, addressee, times, replay, type, capability). No job runs a shell command. Writes need a capability switched on at both ends; root is a per-host opt-in at both ends. |
| Guards | Revoke closes the link at once; audit on both ends; the agent's systemd sandbox and narrow sudo rules. See [Agent protocol](develop/agent-protocol.md). |
| Left | Whoever holds labdex's job-signing key can run any listed job on every agent. |

## Ansible pipes through the agent (Go agent)

| | |
| --- | --- |
| Way in | A signed `ansible.session` job opens a session (at most 60 min); in it labdex opens byte pipes that the agent connects to `127.0.0.1:<ssh_port>` on its host, and nowhere else. |
| Who can use it | Only labdex, in the process that holds the agent's link, through a signed job, on a host whose `agent.conf` allows `ansible` and whose agent an administrator switched `ansible` on for. sshd still authenticates every connection. |
| What it can do | Whatever sshd lets the SSH user do. labdex logs in as `labdex-ansible` with certificates of its own, per run and per host, valid for at most an hour and only from `127.0.0.1`; with become switched on for the host (at both ends), that is full root. |
| Guards | Fixed address and port in the agent; at most 4 pipes per session and 8 in all; pipes close on session end, expiry, a dropped link or revoke; audit lines for every session and pipe (bytes, never content); sshd's own log. |
| Left | labdex holds every key (job signing, SSH CA, vault), so a compromised labdex is root on every host with become. Hardening and backups: [Ansible through the agent](develop/design/ansible-through-the-agent.md#5-risks). |

## SSH keys through the agent (Go agent)

| | |
| --- | --- |
| Way in | A signed `ssh_keys.apply` job makes the agent run its own program as root (one fixed sudo line through `systemd-run`) with the wanted keys on stdin. `ssh_keys.report` does the same, read-only, for every login user's key fingerprints. |
| Who can use it | Only labdex administrators: the job is built from the keys and grants in labdex, and the generic job API refuses it. On a host whose `agent.conf` allows `ssh_keys` and names `ssh_users`, and whose agent an administrator switched `ssh_keys` on for. |
| What it can do | Decide which public keys may log in as the users in `ssh_users`, by writing `/etc/ssh/labdex/<user>.keys` and `<user>.break-glass`. With `--ssh-keys-only` that is everyone who can log in as those users. Never a user outside `ssh_users`, never a home folder, never a key with options. |
| Guards | The root side reads `ssh_users` and `allow` from root-owned `agent.conf` itself; strict key checks at both ends; `/etc/ssh/labdex` must be root's and not writable by others; files replaced at once; files opened without following links; an apply never removes a break-glass key; audit in labdex for every key, grant, break-glass key and apply, and in the agent's log. labdex keeps only public keys; a break-glass private key it makes is shown once and not stored. |
| Left | A compromised labdex (or its job-signing key) can put its own key on every host with `ssh_keys` on, as any user in `ssh_users`. Keep `root` out of `ssh_users` unless needed, and keep the break-glass key offline. |

