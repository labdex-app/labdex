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
| On the host | `install.sh --ansible`: sshd lets `labdex-ansible` in only with a certificate from labdex's CA and a principal for this host, from `127.0.0.1`, without password, TTY or forwarding; root only with `--ansible-become`. `GET /api/agents/ansible-ca.pub` is public: it serves the CA's public key, nothing secret. |
| Guards | Fixed address and port in the agent; at most 4 pipes per session and 8 in all; pipes close on session end, expiry, a dropped link or revoke; audit lines for every session and pipe (bytes, never content); sshd's own log. |
| Left | labdex holds every key (job signing, SSH CA, vault), so a compromised labdex is root on every host with become. Hardening and backups: [Ansible through the agent](develop/design/ansible-through-the-agent.md#5-risks). |
