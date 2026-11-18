# Host agents

The **agent** is a small service on a Linux host that keeps one outgoing connection to labdex. The
host needs no open port. Over that connection the agent:

- sends a heartbeat with metrics every half minute (CPU, load, memory, `/`, uptime, network bytes);
- discovers services: listening ports and the process behind each one (PID, user, command line,
  parent), running systemd units and running Docker or Podman containers;
- reports waiting updates and whether a reboot is needed (the [Patches](patches.md) page);
- runs jobs that labdex signs, from a fixed list. There is no way to send it a shell command.

Writes are off until you switch them on, one capability at a time, in two places: on the
host (when you install the agent) and in labdex (an administrator). Until then the agent only reads.

## Install it

On the host's page, open the **Agent** tab and choose **Enrol an agent**. labdex shows a command with
a one-time token that works for an hour. Run it on the host as root:

```sh
curl -fsSLo install.sh https://lantern.example.net/api/agents/install.sh
sudo sh install.sh --url https://lantern.example.net --token lxe_… --sha256 …
```

Read `install.sh` before you run it. It:

1. installs Python 3 (3.11 or newer), its `cryptography` package and `sudo` from the distribution,
   but only what is missing. The agent brings its other libraries along, so nothing comes from
   PyPI;
2. downloads the agent (`/opt/labdex-agent/labdex-agent.pyz`) from labdex and checks it against
   `--sha256`;
3. creates the system user `labdex-agent` (no shell, no home);
4. writes `/etc/labdex-agent/agent.conf` and the sudo rules in `/etc/sudoers.d/labdex-agent`
   (checked with `visudo` first);
5. swaps the token for the host's own key (`/var/lib/labdex-agent/credentials.json`, mode 0600;
   labdex keeps only a hash of it) and a client certificate (`client.pem`, mode 0600, see
   [below](#client-certificates));
6. installs and starts the systemd service `labdex-agent`.

Options:

| | |
| --- | --- |
| `--allow "patch reboot ansible"` | the writes this host accepts. Without it the agent is read-only. |
| `--root` | run the agent as root instead of `labdex-agent`. labdex must allow root for this host too: only an administrator can tick *run it as root* when making the token. Ansible needs it. |
| `--ca-file /path/ca.pem` | for a labdex with a certificate from your own CA. |
| `--uninstall` | stop and remove the agent again: its service, sudo rules, config and program. The key, certificate and audit log stay in `/var/lib/labdex-agent`; add `--purge` to remove them and the `labdex-agent` user too. Revoke the agent on the Agent tab as well. |

Running it again installs the agent anew with a new token, and revokes the older one.
Without `curl` on the host, it downloads with Python instead; fetch `install.sh` itself the same way:

```sh
python3 -c "import urllib.request as u; open('install.sh','wb').write(u.urlopen('https://lantern.example.net/api/agents/install.sh').read())"
```

The hub URL must be `https://`: the key travels over it.

## What runs with privileges

The agent runs as `labdex-agent`. Its sudo rules allow exactly these command lines and nothing else
(see them with `labdex-agent sudoers`):

| Command | Why | When |
| --- | --- | --- |
| `ss -Htulpn` | which process listens on a port | always (read-only) |
| `docker ps …`, `podman ps …` | running containers | always (read-only) |
| `systemd-run … apt-get update`, `… apt-get dist-upgrade`, `… dnf upgrade` | install updates | with `patch` |
| `shutdown -r +1` | reboot after updates | with `reboot` |

The agent's user is never put in the `docker` group, because that is as good as root. Write jobs run
through `systemd-run`, outside the agent's sandbox, one at a time, and show up in the journal as
`labdex-agent-patch` or `labdex-agent-ansible`.

The service runs in a systemd sandbox: `ProtectSystem=strict` (the only folder it can write is
`/var/lib/labdex-agent`), `ProtectHome`, `PrivateTmp`, `ProtectControlGroups`, a capability bounding
set that holds only what sudo needs to become root and what `ss` needs to see a socket's process,
and limits on memory and tasks (see `backend/labdex/agent/dist/labdex-agent.service`).

Some hardening is left off on purpose. With a user of its own, systemd turns on
`NoNewPrivileges` by itself as soon as a service has any of `PrivateDevices`,
`ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectClock`,
`ProtectHostname`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictAddressFamilies`,
`LockPersonality`, `MemoryDenyWriteExecute` or `SystemCallArchitectures`. Under
`NoNewPrivileges`, sudo can't run, so every sudo rule above would fail. This was checked on
Debian 12 with systemd 252.

## Jobs

| Job | Writes? | What it does |
| --- | --- | --- |
| `ping` | no | answers, to test the link |
| `facts` | no | OS, kernel, CPU, memory, disks |
| `discovery.services` | no | listening ports with their processes, systemd units, containers |
| `patch.report` | no | updates waiting, reboot needed |
| `patch.apply` | `patch` (and `reboot` to reboot) | install the waiting updates, then report again |
| `ansible.run` | `ansible`, and a root agent | run a playbook from the site's git repository on this host |

Every job is signed with labdex's own key (`<data>/agent-signing.key`). The agent saved the public
half when it enrolled. It runs a job only when all of these hold:

- the signature is good;
- the job is for this agent;
- it hasn't expired (ten minutes);
- it hasn't run before;
- it is on the list above;
- for a write, the capability is on in `agent.conf`.

A job that fails a check is refused, and the refusal goes back to labdex.

**Discovery** and **update reports** run on a schedule (every hour and every six hours by
default, changeable on the Agent tab, `0` turns them off) and on demand. Discovered ports go to the
**Discovery** page (source `agent`) for each of the host's addresses they listen on; ports that
listen only on loopback stay on the host's Agent tab. Passwords, tokens and keys in command lines
are masked before they leave the host (`--password ***`, `DB_PASS=***`, `https://user:***@…`).

**Ansible** runs never carry a playbook or a command. labdex packs the folder that holds the playbook
from the site's git working copy at its current commit (a `.tar.gz`, at most 8 MB). It sends the
agent the bundle's SHA-256, the commit, the playbook path and the variables, all signed. The agent
fetches the bundle over the same connection and checks the hash. It unpacks the bundle with no
absolute paths, no `..` and no links, then runs `ansible-playbook -i localhost, -c local` through
`systemd-run`. Tick *check only* for `--check --diff`.

## Revoke

**Revoke** on the Agent tab stops the key at once: labdex closes the live connection within a
second, and the agent stops and stays stopped (`RestartPreventExitStatus=3`). Enrolling a host again
revokes its older agent.

## Client certificates

On top of its key, each agent has a client certificate for mutual TLS. labdex is a small
certificate authority for its own agents and nothing else (`<data>/agent-ca.key` and
`agent-ca.pem`). At enrolment the agent makes a key pair on the host and sends a certificate
request with the token. labdex signs a certificate for a year, named after the agent, and keeps
its SHA-256. The private key never leaves the host. On every link the agent presents the
certificate in the TLS handshake, and labdex checks that it is from this CA, in date, for this
agent and the one on record. Thirty days before it runs out, labdex asks the agent for a new
one over the link. The old one keeps working until the agent links with the new one. **Revoke**
makes the key and the certificate worthless at once, so there is no revocation list.

`LABDEX_AGENT_MTLS` decides how strict this is:

| | |
| --- | --- |
| `optional` (default) | agents get certificates; a certificate that is presented must be right, but a link without one still works |
| `required` | no link and no enrolment without a certificate. Switch to it once the Agent tab shows *used on this link* for your agents |
| `off` | keys only |

`optional` doesn't protect against a stolen key: only `required` does. The Agent tab shows the
certificate's expiry and whether the current link presented it.

The certificate has to reach labdex from whoever terminates TLS:

- **labdex itself**, with gunicorn's own TLS: add `--cert-reqs 1 --ca-certs /data/agent-ca.pem`
  to its `--certfile`/`--keyfile`.
- **A reverse proxy**: it asks for a client certificate, checks it against labdex's CA
  (`/api/agents/ca.pem`), and passes it on in a header. Name the header in
  `LABDEX_AGENT_CERT_HEADER` and put the proxy in `LABDEX_TRUSTED_PROXIES`. The proxy must
  overwrite the header on every request, so a client can't set it. Enrolment comes without a
  certificate, so make it optional, not required, at the proxy.

**Caddy**

```caddy
lantern.example.net {
    tls {
        client_auth {
            mode verify_if_given
            trust_pool file /etc/caddy/labdex-agent-ca.pem
        }
    }
    reverse_proxy labdex:8000 {
        header_up X-Client-Cert {http.request.tls.client.certificate_der_base64}
    }
}
```

**nginx**

```nginx
ssl_client_certificate /etc/nginx/labdex-agent-ca.pem;
ssl_verify_client optional;
location / {
    proxy_set_header X-Client-Cert $ssl_client_escaped_cert;
    proxy_pass http://labdex:8000;
}
```

**Traefik**

```yaml
tls:
  options:
    labdex-agents:
      clientAuth:
        caFiles: [/etc/traefik/labdex-agent-ca.pem]
        clientAuthType: VerifyClientCertIfGiven
http:
  middlewares:
    labdex-agent-cert:
      passTLSClientCert:
        pem: true   # sent as X-Forwarded-Tls-Client-Cert
```

With Traefik, set `LABDEX_AGENT_CERT_HEADER=X-Forwarded-Tls-Client-Cert`.

## Offline

When labdex can't be reached, the agent retries with backoff up to a minute. Job results and
heartbeats wait in `/var/lib/labdex-agent/buffer` (at most 64 MB and seven days) and go out, oldest
first, when the connection is back. labdex ignores a result it already has. Jobs that waited more
than ten minutes for an agent that wasn't connected expire.

## Audit

labdex records enrolment tokens, enrolments, revocations, settings changes, every job you start and
its outcome in the history of the host (object type `agent`). Scheduled read-only runs are recorded
only when they fail. The agent writes its own log, `/var/lib/labdex-agent/audit.log`: one JSON line
for every job it accepted, refused or finished, without the job's output.

## Metrics

`GET /api/agents/metrics` returns each connected agent's latest heartbeat in the Prometheus text
format, as `labdex_agent_*` with `host` and `agent` labels. Point VictoriaMetrics at it with an API
token. labdex itself keeps no history of these numbers.

## On the host

```sh
systemctl status labdex-agent
labdex-agent health                 # exit 0 while labdex answered in the last two minutes
labdex-agent show discovery         # what discovery would report, printed here, nothing sent
labdex-agent sudoers                # the sudo rules for this host's capabilities
```

`labdex-agent` (in `/usr/local/bin`) runs `/usr/bin/python3 -I /opt/labdex-agent/labdex-agent.pyz`.
Its log is in the journal: `journalctl -u labdex-agent`. Write jobs log there too, as
`labdex-agent-patch` and `labdex-agent-ansible`.

## Running labdex for agents

Each connected agent holds one of the web server's threads. The image starts gunicorn with 64
threads. With many agents, raise `--threads` in the container's command. Behind a reverse
proxy, pass WebSockets through for `/api/agents/link`.

The agent replaces `labdex-patch-report`, which still works for hosts without an agent.
