# Agent protocol

This is the contract between labdex (the **hub**) and the host agent. Two agents implement it: the
Python agent in `backend/labdex/agent/` and the Go agent in `agent/`. They share no code with each
other or with the hub, so this page is what they agree on. Where this page and the Python agent
differ, the Python agent's behaviour wins until this page is fixed: it is what runs today.

User-facing docs: [Host agents](../guide/agent.md) and [A first agent, read-only](../guide/agent-trial.md).

The key words MUST, MUST NOT, SHOULD and MAY mean what RFC 2119 says.

## Overview

```text
host                                                         labdex
┌────────────────────────────┐   1. POST /api/agents/enrol   ┌────────────────────┐
│ labdex-agent               │ ─── token + CSR ────────────▶ │ key hash, cert,    │
│  credentials.json          │ ◀── key, signing key, cert ── │ signing key (Ed25519)
│  client.pem (mTLS)         │                               │                    │
│                            │   2. wss://…/api/agents/link  │                    │
│  link loop  ───────────────┼── hello, heartbeat, result ─▶ │ job queue          │
│  job pool (2) ◀────────────┼── welcome, signed job ─────── │ audit              │
│  buffer/  (offline spool)  │                               │                    │
│  audit.log, seen.json      │                               │                    │
└────────────────────────────┘                               └────────────────────┘
```

- The agent dials out. The host needs no open port.
- One-time **enrolment token** → per-host **key** (`lxa_…`) and an mTLS **client certificate**.
- Jobs are **signed** by the hub (Ed25519) and come from a fixed **allowlist**. There is no shell
  and no way to send a command line.
- A job that changes the host needs its **capability** switched on at **both ends**.
- Results and heartbeats go to a disk **buffer** while the hub is unreachable.
- Both ends keep an **audit** record of every job.

## Versions

| Item | Value |
| --- | --- |
| Protocol version | `1` |
| Job envelope version (`v`) | `1` |

The protocol version is sent at enrolment and on every link. A hub that speaks another version
answers enrolment with HTTP 426 and closes the link with close code 4426. A change that an older
peer would misread MUST bump the protocol version. Adding an optional field to a message is not
such a change: both ends MUST ignore fields they don't know.

## Constants

| Name | Value | Meaning |
| --- | --- | --- |
| `HEARTBEAT` | 30 s | Default heartbeat interval; the hub may set another in `welcome` |
| `MAX_SKEW` | 300 s | How far a job's times may be off between hub and host clocks |
| `JOB_TTL` | 600 s | A queued job not sent by then expires on the hub |
| `MAX_MESSAGE` | 16 MiB | Largest WebSocket message either side accepts |
| `CONNECT_TIMEOUT` | 15 s | TCP + TLS + WebSocket handshake |
| `LINK_IDLE` | 90 s | No message from the hub this long: the link is dead, reconnect |
| `MAX_BACKOFF` | 60 s | Longest wait between link attempts |
| `HEALTHY_FOR` | 120 s | `health` is OK while the hub answered within this time |
| `BUNDLE_WAIT` | 120 s | How long `ansible.run` waits for its bundle |
| WebSocket ping | 25 s | Both ends send WebSocket pings at this interval |

## Files on the host

| Path | Owner, mode | What |
| --- | --- | --- |
| `/etc/labdex-agent/agent.conf` | root, 0644 | Config (below) |
| `/var/lib/labdex-agent/` | agent user, 0700 | State folder (`state_dir`) |
| `credentials.json` | 0600 | Agent id, key, host, hub URL, hub's job-signing public key |
| `client.pem` | 0600 | Client certificate followed by its private key (PKCS#8 PEM) |
| `seen.json` | 0600 | Ids of jobs already taken, until they expire |
| `audit.log` | 0600 | One JSON line per job event |
| `buffer/` | 0700 | The offline spool |
| `heartbeat` | default | Unix time the hub last answered, as text |
| `bundles/<sha256>/` | 0700 | An unpacked Ansible bundle while its job runs |

Secret files MUST be written to `<name>.tmp` with mode 0600, synced, then renamed over the old
file, so a reader never sees half a file.

### agent.conf

An INI file with one section:

```ini
[agent]
url = https://lantern.example.net
# write capabilities this host accepts: patch, reboot, ansible (space or comma separated); empty: read-only
allow =
# a CA bundle for a hub with a private certificate authority
ca_file =
state_dir = /var/lib/labdex-agent
# the one port ansible.session pipes reach, on 127.0.0.1 (Go agent)
ssh_port = 22
# the only users whose SSH keys labdex may manage, with ssh_keys in allow (Go agent)
ssh_users =
```

An unknown capability in `allow` is a fatal config error: the agent MUST exit non-zero and say
which. So is an `ssh_port` that isn't a number from 1 to 65535, and an `ssh_users` entry that
isn't a user name (`^[a-z_][a-z0-9_.-]{0,31}$`). A trailing `/` on `url` is dropped. `#` and `;` start comment lines.

### credentials.json

Written at enrolment. The agent MUST NOT change it afterwards.

```json
{
  "agent_id": "1a2b3c4d",
  "key": "lxa_1a2b3c4d_<secret>",
  "host": "srv-vm-dns-01",
  "signing_key": "<base64 of the 32-byte Ed25519 public key>",
  "protocol": 1,
  "url": "https://lantern.example.net"
}
```

`url` is the hub URL used at enrolment. The link uses it, not `agent.conf`'s, so editing the config
can't point an enrolled agent at another hub.

## Enrolment

The agent swaps a one-time token (`lxe_…`, valid an hour, made on the host's Agent tab) for its own
key.

1. The URL MUST be `https://` (a test-only flag MAY allow `http://`).
2. The agent makes a new **EC P-256** key and a CSR for it with subject `CN=labdex-agent`, signed
   with SHA-256. The hub uses only the public key; it names the certificate after the agent.
3. `POST <url>/api/agents/enrol` with headers `Content-Type: application/json` and
   `X-Requested-With: labdex-agent`:

    ```json
    {"token": "lxe_…", "hostname": "<gethostname>", "version": "<agent version>", "csr": "<PEM>",
     "protocol": 1, "root": false, "allow": ["patch"]}
    ```

    `root` is whether the agent runs as uid 0. `allow` is the sorted capability list from
    `agent.conf`.

4. The agent MUST NOT follow redirects (the token would go along), MUST use TLS 1.2 or newer and
   MUST trust the system roots, or only `ca_file` when it is set. Timeout: 30 s.
5. Answers:

    | Status | Meaning | Agent |
    | --- | --- | --- |
    | 200 | Enrolled | Save (below) |
    | 403 | Bad, used or expired token; root not allowed; a certificate is required | Exit non-zero with the hub's text |
    | 426 | Protocol version mismatch | Same |

    The 200 body:

    ```json
    {"agent_id": "1a2b3c4d", "key": "lxa_…", "host": "srv-vm-dns-01", "signing_key": "<base64>",
     "protocol": 1, "client_cert": "<PEM, when mTLS is not off>", "ca_cert": "<PEM>"}
    ```

    `agent_id`, `key`, `host` and `signing_key` MUST be non-empty strings, or enrolment fails.

6. The agent removes any old `client.pem`, saves `client_cert` with the key from step 2 in
   `client.pem` (after checking the certificate's public key is that key), drops `client_cert` and
   `ca_cert`, adds `url` and writes `credentials.json`.

A new enrolment for a host revokes the host's older agent on the hub.

## The link

The agent keeps one WebSocket connection to `<url>/api/agents/link`, with `https` → `wss` (and
`http` → `ws` in tests).

### Handshake

Request headers:

| Header | Value |
| --- | --- |
| `Labdex-Agent-Key` | The key from `credentials.json` |
| `Labdex-Agent-Version` | The agent's version |
| `Labdex-Agent-Protocol` | `1` |

TLS: version 1.2 or newer, the system roots or only `ca_file` when set, and the certificate in `client.pem`
presented when the server (or a proxy in front) asks for one. The agent MUST NOT follow
redirects.

An HTTP 401, 403 or 426 answer to the upgrade means the hub won't take this agent: stop for good
(exit code 3, below).

### Messages

Every message is one WebSocket **text** frame holding one JSON object with a type field `t`.
Unknown types and fields MUST be ignored. Text that isn't a JSON object MUST be ignored.

Agent → hub:

| `t` | Fields | When |
| --- | --- | --- |
| `hello` | `version`, `protocol`, `hostname`, `root` (bool), `allow` (sorted list), `jobs` (sorted list of job types it runs), `facts` (object, see `facts`), `buffered` (records in the spool), `certs: true`, `cert_sha256` (hex SHA-256 of the DER of the certificate in `client.pem`, `""` for none) | First message on every link |
| `heartbeat` | `ts` (Unix time, float), `metrics` (object) | Every heartbeat interval |
| `ack` | `id` | A job passed every check and is about to run |
| `result` | `id`, `ok`, `result`, `error`, `started`, `finished`; or `id`, `ok: false`, `refused: true`, `error` | A job finished or was refused |
| `csr` | `csr` (PEM) | Answer to `renew` |
| `bundle.get` | `sha256` | `ansible.run` needs its bundle |
| `pipe.opened` | `id` | A pipe reached sshd ([Ansible over the link](#ansible-over-the-link)) |
| `pipe.data` | `id`, `data` (base64, at most `PIPE_CHUNK` bytes decoded) | Bytes from sshd |
| `pipe.close` | `id`, `error` (absent on a normal end) | A pipe ended or was refused |

Hub → agent:

| `t` | Fields | Agent does |
| --- | --- | --- |
| `welcome` | `host`, `heartbeat` (s), `server_time` | Set the heartbeat interval to `heartbeat` clamped to 1…300 (30 if missing or 0); replay the spool |
| `ok` | — | Nothing (it counts as contact) |
| `job` | `job` (JSON text), `sig` (base64) | Check and run it ([Jobs](#jobs)) |
| `renew` | — | Make a new key and CSR, send `csr`, keep the key until `cert` arrives |
| `cert` | `cert` (PEM), `ca`; or `error` | Save `cert` with the pending key in `client.pem`; used from the next link |
| `bundle` | `sha256`, `data` (base64); or `sha256`, `error` | Hand the bytes (or the error) to the waiting `ansible.run` |
| `pipe.open` | `id`, `session` | Open a pipe to sshd in that session, answer `pipe.opened` or `pipe.close` |
| `pipe.data` | `id`, `data` (base64) | Write the bytes to that pipe's sshd connection |
| `pipe.close` | `id` | Close that pipe; no answer |
| `session.end` | `session` | Close the session's pipes (each answered with `pipe.close`) and forget it |

A record replayed from the spool carries `"buffered": true`. The hub uses a buffered heartbeat's
time for "last seen" but not its metrics, and doesn't answer it with `ok`.

### Close codes

| Code | Meaning | Agent |
| --- | --- | --- |
| 4401 | Unknown or revoked key | Stop for good (exit 3) |
| 4403 | Runs as root and the host isn't allowed a root agent | Stop for good (exit 3) |
| 4426 | Protocol version mismatch | Stop for good (exit 3) |
| 4495 | No client certificate, or the wrong one | Reconnect with backoff (a proxy may get fixed) |
| other | — | Reconnect with backoff |

### Reconnecting

On any other end of the link (network error, close, `LINK_IDLE` with no message), the agent waits
and tries again: 1 s, then doubling up to `MAX_BACKOFF`. A link that stayed up more than 60 s
resets the wait to 1 s. Every message from the hub counts as contact: the agent writes the Unix
time to `heartbeat` (at most every 10 s) for `health`.

## Jobs

### The envelope

The hub signs the exact bytes of a JSON text, so neither side has to agree on JSON serialisation:

```json
{"t": "job", "job": "{\"agent\":\"1a2b3c4d\",\"by\":\"admin\",\"expires\":1764000600,…}", "sig": "<base64>"}
```

The envelope inside `job`:

| Field | Type | |
| --- | --- | --- |
| `v` | int | `1` |
| `id` | string | Job id (UUID) |
| `agent` | string | Agent id it is addressed to |
| `host` | string | Host name in labdex |
| `type` | string | A job type below |
| `params` | object | Its parameters |
| `issued` | number | Unix time |
| `expires` | number | Unix time |
| `by` | string | Who queued it (for the audit) |

`sig` is the standard base64 (with padding) of the Ed25519 signature over the UTF-8 bytes of `job`.
The public key is `signing_key` from `credentials.json`: standard base64 of the raw 32 bytes.

### Checks

The agent checks in this order and stops at the first failure. The texts go to both audit logs
and SHOULD be the same in every agent:

| # | Check | Error text |
| --- | --- | --- |
| 1 | `job` and `sig` are strings | `not a signed job` |
| 2 | Signature valid (bad base64 counts as invalid) | `bad signature` |
| 3 | Envelope is an object with `v == 1` | `unknown job format` |
| 4 | `agent` is this agent's id | `addressed to another agent` |
| 5 | `issued` and `expires` are numbers | `job without times` |
| 6 | `issued <= now + MAX_SKEW` | `issued in the future: check the clocks` |
| 7 | `expires >= now - MAX_SKEW` | `expired` |
| 8 | `type` is a known job type | `unknown job type '<type>'` |
| 9 | `params` is an object and `id` a string | `job without id or params` |
| 10 | Every capability the job needs is in `allow` | `capability not switched on in this host's agent.conf: <caps, comma separated>` |
| 11 | `id` not in `seen.json` | `job already run (replay)` |

Failures 1–9 are written to `audit.log` as `refused` and, when the job text holds a string `id`,
answered with a refused `result` (sent, or buffered when the link is down). Failure 10 is the
same. Failure 11 is written to `audit.log` but **not answered**: the hub already has the first
answer.

When every check passes, the agent:

1. adds `id` to `seen.json` with its `expires`;
2. writes `accepted` to `audit.log` with `id`, `type`, `params`, `by`;
3. sends `ack` (not buffered);
4. runs the job in a pool of **two** workers. Write jobs (`patch.apply`, `ansible.run`) also take
   a single write lock, so at most one write job runs at a time.

When the handler returns, the agent writes `finished` to `audit.log` (`id`, `type`, `ok`, `error`
cut to 300 characters, `seconds`) and sends the result, or buffers it:

```json
{"t": "result", "id": "…", "ok": true, "result": {…}, "error": "", "started": 1764000000.1, "finished": 1764000003.4}
```

`ok` is the handler's `result.ok` when the result is an object that has it, else `true`. `error` is
`result.error` when set. A handler that fails gives `ok: false`, `result: null` and the error. A
failing job MUST NOT stop the agent.

`seen.json` is a JSON object `{"<id>": <expires>}`. Entries with `expires + MAX_SKEW <= now` are
dropped whenever an id is added.

### Job types

Nothing else runs. `write` is the capability a job needs at both ends.

| Type | Write | Hub timeout | Parameters | Result |
| --- | --- | --- | --- | --- |
| `ping` | — | 10 s | `echo` (optional) | `{"pong": true, "time": <now>, "echo": "<echo, max 200 chars>"}` |
| `facts` | — | 30 s | none | [Facts](#facts) |
| `discovery.services` | — | 120 s | none | [Discovery](#discovery) |
| `patch.report` | — | 600 s | none | [Patch report](#patch-report) |
| `patch.apply` | `patch` (+ `reboot` when `params.reboot`) | 3600 s | `reboot` (bool) | [Patch apply](#patch-apply) |
| `ansible.run` | `ansible` | 3600 s | `bundle`, `playbook`, `commit`, `vars`, `check` | [Ansible](#ansible) |
| `ansible.session` | `ansible` | 3600 s | `session`, `minutes` | [Ansible over the link](#ansible-over-the-link); Go agent only |
| `ssh_keys.report` | — | 120 s | none | [SSH keys](#ssh-keys); Go agent only |
| `ssh_keys.apply` | `ssh_keys` | 300 s | `users`, `break_glass` (optional) | [SSH keys](#ssh-keys); Go agent only |

Capabilities: `patch`, `reboot`, `ansible`, and `ssh_keys` (Go agent only). The hub refuses to queue a job whose capability is off
in labdex (an administrator's switch) or missing from the `allow` the agent reported in `hello`.
The agent checks its own `allow` again (check 10).

## Privileges

The agent runs as the system user `labdex-agent` by default, or as root where the host opted in
(`install.sh --root`, and `allow_root` set by an administrator in labdex). A non-root agent runs
the privileged commands below through `sudo -n` (never asking for a password); a root agent runs
them directly. Nothing else runs privileged.

| Name | Capability | Command |
| --- | --- | --- |
| `ss` | — | `ss -Htulpn` |
| `docker_ps` | — | `docker ps --no-trunc --format {{json .}}` |
| `podman_ps` | — | `podman ps --format json` |
| `apt_update` | `patch` | `systemd-run --quiet --wait --pipe --collect --unit=labdex-agent-patch --setenv=DEBIAN_FRONTEND=noninteractive apt-get -q update` |
| `apt_upgrade` | `patch` | `systemd-run … --unit=labdex-agent-patch --setenv=DEBIAN_FRONTEND=noninteractive apt-get -q -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade` |
| `dnf_upgrade` | `patch` | `systemd-run … --unit=labdex-agent-patch dnf -q -y upgrade` |
| `reboot` | `reboot` | `shutdown -r +1 labdex-agent: reboot after updates` |
| `ssh_keys_report` | — | `systemd-run … --unit=labdex-agent-ssh-keys /opt/labdex-agent/labdex-agent ssh-keys report` (Go agent) |
| `ssh_keys_apply` | `ssh_keys` | `systemd-run … --unit=labdex-agent-ssh-keys /opt/labdex-agent/labdex-agent ssh-keys apply` (Go agent; the keys on stdin) |

The program (and, for `systemd-run`, the program it starts) is resolved to an absolute path:
`argv[0]` on `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`, the started program
on `/usr/sbin:/usr/bin:/sbin:/bin`. Write jobs go through `systemd-run` so they run outside the
agent's own sandbox (`ProtectSystem=strict` would make `/usr` read-only for apt too).

Every command runs with no stdin (except `ssh_keys_apply`, which reads its JSON there), the environment `PATH=/usr/sbin:/usr/bin:/sbin:/bin` and
`LC_ALL=C` only, and a timeout. A missing program gives exit code 127, a timeout 124, another start
failure 126.

### sudoers

`labdex-agent sudoers [--user U]` prints the rules for exactly these lines: every read-only one, and
the write ones whose capability is in `allow`. `install.sh` checks the output with `visudo -cf`
before it goes live.

```text
# labdex-agent: written by `labdex-agent sudoers`; the agent runs nothing else with sudo.
Defaults:labdex-agent !requiretty, !lecture
labdex-agent ALL=(root) NOPASSWD: /usr/bin/ss -Htulpn
# docker_ps: docker is not installed here
…
```

Each rule is `<user> ALL=(root) NOPASSWD: ` and the resolved command line, with `,` `:` `=` `\`
escaped by a backslash. A command whose program is missing gives a comment line
`# <name>: <program> is not installed here` instead.

## Facts

Read from `/proc`, `/etc/os-release` and `statvfs`: no commands, no root.

| Field | Source |
| --- | --- |
| `hostname` | `gethostname` |
| `fqdn` | Fully qualified name, or `hostname` |
| `os` | `PRETTY_NAME` from `/etc/os-release`, else the kernel name |
| `os_id`, `os_version` | `ID`, `VERSION_ID` |
| `kernel` | `uname -r` |
| `arch` | `uname -m` |
| `cpus` | Logical CPUs |
| `memory` | `MemTotal` in bytes |
| `disks` | `[{mount, total, used, free}]` in bytes, for each mount of a real file system (below) |
| `user` | The agent's user name |
| `root` | Running as uid 0 |
| `boot_time` | `now - uptime`, rounded |
| `python` | Python agent only: its Python version |
| `agent` | Go agent only: `"go"` |
| `ssh_host_keys` | Go agent only: `<type> <base64>` of each `/etc/ssh/ssh_host_*_key.pub` whose type starts with `ssh-` or `ecdsa-`, sorted, comments dropped; `[]` without sshd |

Real file systems: ext2, ext3, ext4, xfs, btrfs, zfs, vfat, f2fs, jfs, reiserfs, bcachefs, from
`/proc/mounts` (`\040` → space), each mount point once, only directories (a bind-mounted file is no
disk). None: `["/"]`. `used = blocks × frsize − bfree × frsize`, `free = bavail × frsize`.

## Metrics

Sent in every heartbeat. All numbers.

| Field | |
| --- | --- |
| `cpu_percent` | CPU busy since the last heartbeat, one decimal (busy = total − idle − iowait from the `cpu` line of `/proc/stat`) |
| `load1`, `load5`, `load15` | Load averages, two decimals |
| `mem_total`, `mem_available`, `swap_total`, `swap_free` | From `/proc/meminfo`, bytes |
| `root_total`, `root_used` | `/`, bytes |
| `uptime` | Seconds, rounded |
| `net_rx_bytes`, `net_tx_bytes` | Sum over every interface but `lo` in `/proc/net/dev` |

The hub exports them at `/api/agents/metrics` (Prometheus text format).

## Discovery

`discovery.services` is read-only. Result:

```json
{"collected_at": 1764000000, "listeners": [...], "units": [...], "containers": [...],
 "processes": [...], "notes": ["..."]}
```

**Listeners.** From `ss -Htulpn` (privileged); when that fails, from `ss -Htuln` (no processes) with
a note:

- stderr holds `no new privileges` → `no processes for listening ports: sudo can't run under NoNewPrivileges (check the service's hardening)`;
- stderr holds `sudo` or the exit code is 1 → `no processes for listening ports: the sudo rule for ss is missing`;
- else `ss with processes failed: <stderr, 200 chars>`.

Parsing `ss`: split each line on whitespace. Keep lines with at least 5 fields, field 1 `tcp` or
`udp` and field 2 `LISTEN` or `UNCONN`. Field 5 is `addr:port`: split at the last `:`, the port must
be digits, strip `[]` and anything from `%`, `*` or empty is `0.0.0.0`. Processes are every
`("name",pid=N,fd=N)` in the fields from 7 on, one per PID. A `(proto, address, port)` seen before
is skipped. Each listener: `{proto, address, port, pids: [{name, pid}]}` sorted by PID; the list is
sorted by port, proto, address. After joining with processes, each listener gets `units` (sorted
unit names of its processes) and, when a container publishes the same proto and port, `container:
{engine, name, image}`.

**Processes.** The PIDs of every listener and every unit's main PID, at most 500 (lowest first),
plus each one's parent when the parent isn't PID 1 and the 500 aren't reached. From `/proc/<pid>/`:

| Field | Source |
| --- | --- |
| `pid`, `ppid` | `status` (`PPid`) |
| `name` | `status` (`Name`) |
| `uid`, `user` | First `Uid` value; user from the password database, else the number |
| `cmdline` | `cmdline` split on NUL, empties dropped, [redacted](#redaction), joined by spaces, cut to 1000 characters; empty → `[<name>]` |
| `unit` | Innermost `*.service` or `*.scope` in `cgroup`, else `""` |
| `started` | Boot time + field 22 of `stat` (field 20 after the `)`) ÷ clock ticks per second, rounded |
| `parent` | `{pid, name}`: the parent's name when collected, `systemd` for PID 1, else `""` |

Sorted by PID.

**Units.** When `systemctl` is there: `systemctl list-units --type=service --state=running
--no-legend --plain --no-pager`, first word of each line; then `systemctl show --no-pager -p
Id,Description,MainPID,User,FragmentPath,ActiveEnterTimestamp -- <names>`. Blocks are separated by
an empty line; each gives `{unit, description, pid (null for 0), user ("root" when empty), path,
since}`. No systemd: note `no systemd here`.

**Containers.** For each of `docker` and `podman` that is installed, the privileged `docker_ps` or
`podman_ps`. A failure gives the note `<engine> ps failed (is the sudo rule there?): <stderr>`.

- Docker, one JSON object per line: `{engine: "docker", id: ID[:12], name: Names, image: Image,
  status: Status, compose_project: <com.docker.compose.project from Labels>, ports}`. `Ports` text
  like `0.0.0.0:8080->80/tcp, [::]:8080-8081->80-81/tcp` gives one `{address, port,
  container_port, proto}` per host port (ranges expanded), address without `[]`, `0.0.0.0` when
  missing.
- Podman, one JSON array: `{engine: "podman", id: Id[:12], name: Names[0], image, status: Status
  or State, compose_project: Labels[...], ports, pid: Pid}`; ports from `host_ip`, `host_port`,
  `container_port`, `protocol`, `range`.

### Redaction

Command lines MUST NOT leak secrets. A secret word is
`[\w.-]*(pass|passwd|password|passphrase|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credential|credentials|auth)[\w.-]*`,
case-insensitive. For each argument:

1. After a bare secret flag (`--password`), the next argument becomes `***`.
2. `--?<secret word>` alone: keep it, hide the next. With `=value`: `<flag>=***`.
3. `<secret word>=value` or `<secret word>:value` → `<word>=***` / `<word>:***`.
4. Else any `scheme://user:password@` → `scheme://user:***@`.

The [conformance vectors](#conformance) hold the exact cases.

## Patch report

`patch.report` reads what the package manager already knows; it never refreshes the lists (that
needs root; `patch.apply` does it). Result:

```json
{"os": "Debian GNU/Linux 12 (bookworm)", "kernel": "6.1.0-27-amd64", "packages": [{"name": "openssl", "from": "3.0.14-1~deb12u2", "to": "3.0.15-1~deb12u1", "security": true}],
 "pending": 1, "reboot": false, "patched_at": "2026-11-20T03:12:44+00:00"}
```

`error` is added when the package manager failed. The hub takes `pending`, `security`, `reboot`,
`packages`, `os`, `kernel`, `patched_at`, `error`.

- **apt** (`apt-get` on the path): `apt-get -s -o Debug::NoLocking=1 dist-upgrade`; each line
  matching `^Inst (\S+) (?:\[(\S+)\] )?\((\S+) ([^)]*)\)` is a package; `security` when the last
  group holds `-security` or `Debian-Security`. Non-zero exit: `error: apt-get -s dist-upgrade
  failed`. `reboot` when `/var/run/reboot-required` exists, or on Proxmox VE (`pveversion` on the
  path) when the newest `/boot/vmlinuz-*` isn't the running kernel. `patched_at`: the
  `Start-Date` of the last entry with an `Upgrade:` line in `/var/log/apt/history.log`
  (`%Y-%m-%d  %H:%M:%S`, local time) as ISO 8601 with offset, else `null`.
- **dnf/yum**: `<dnf> -q --cacheonly check-update` (exit 0 or 100 is fine), `<dnf> -q --cacheonly
  updateinfo list --security --available`. A check-update line with exactly three fields and a `.`
  in the first is a package: name is the first field without its `.arch`; `to` is the second;
  `from` is `""`. Security when its name is in the security list (the last field of each line with
  three or more fields, without `-<version>-<release>`). `reboot` when `needs-restarting -r` exits
  1. `patched_at`: newest `rpm -qa --qf %{INSTALLTIME}\n` as UTC ISO 8601.
- Neither: `error: no apt-get, dnf or yum here`, `packages: []`.

`os` is `PRETTY_NAME`; on Proxmox VE it becomes `Proxmox VE <version> (<PRETTY_NAME>)` from
`pveversion`. `pending` is the number of packages. Commands run with `LC_ALL=C`, without sudo.

### Patch apply

Needs `patch` (and `reboot` for `params.reboot`). Parameters other than `reboot` are refused
(`unknown parameters: …`). Under the write lock:

1. apt: `apt_update`, then `apt_upgrade`; dnf: `dnf_upgrade`; neither: fail with `no apt-get or
   dnf on this host`.
2. Each step adds `{step, code, output: last 4000 characters of stdout+stderr}`. A non-zero code
   stops: `{ok: false, steps, error: "<step> failed (<code>)"}`.
3. `apt-get update` exits 0 even when a mirror fails, so after `apt_update` any output line starting
   with `Err:`, `E: `, `W: Failed to fetch` or `W: Some index files failed` stops with `apt_update
   couldn't fetch every package list (see its output)`.
4. A fresh patch report; when `params.reboot` and the report says `reboot`, run `reboot` (in a
   minute).

Result: `{ok: true, steps, report, rebooting}`.

## Ansible

`ansible.run` runs a playbook from the site's git repository on this host only. The hub never
sends a playbook text or a command: the signed job names a bundle by its SHA-256.

Parameters (anything else is refused):

| Field | Rule |
| --- | --- |
| `bundle` | `^[0-9a-f]{64}$` |
| `playbook` | `^[A-Za-z0-9_][A-Za-z0-9_./-]*\.ya?ml$`, no `..` part |
| `commit` | Git commit, reported back |
| `vars` | Object, passed as extra vars |
| `check` | Bool: `--check --diff` |

1. The agent must run as root (`ansible.run needs the agent to run as root on this host (install
   with --root)`) and `ansible-playbook` must be installed.
2. Send `bundle.get`; wait up to `BUNDLE_WAIT` for `bundle`. The hub sends only a bundle named in a
   running job of this agent.
3. At most 8 MiB, and its SHA-256 MUST equal `bundle`.
4. Unpack the `.tar.gz` into `<state_dir>/bundles/<sha>/`: regular files and folders only, no
   absolute paths, no `..`, every path inside the folder, at most 64 MiB unpacked; folders 0700,
   files 0600.
5. Write `vars` to `.labdex-vars.json` in that folder.
6. Under the write lock: `systemd-run --quiet --wait --pipe --collect --unit=labdex-agent-ansible
   --working-directory=<dir> --setenv=ANSIBLE_NOCOLOR=1 --setenv=ANSIBLE_RETRY_FILES_ENABLED=0
   <absolute ansible-playbook> -i localhost, -c local -e @<varfile> [--check --diff] <playbook>`,
   timeout 3300 s.
7. Remove the folder.

Result: `{ok: code == 0, code, check, commit, recap: from "PLAY RECAP" (2000 chars), output: last 20000 chars}`.

Ansible itself is Python, so a host that runs Ansible jobs needs Python whichever agent starts it.

## Ansible over the link

Go agent only, so the hub sends `ansible.session` only to an agent whose `hello` lists it in
`jobs`. labdex runs `ansible-playbook` itself, with SSH certificates of its own that live for one
run, and reaches the host's sshd through **pipes**: byte streams inside the link. The agent only
moves bytes; it never sees a key, a playbook or a variable. The design and its reasons are in
[Ansible through the agent](design/ansible-through-the-agent.md).

| Constant | Value | Meaning |
| --- | --- | --- |
| `MAX_SESSION_MINUTES` | 60 | Longest session |
| `MAX_PIPES_PER_SESSION` | 4 | Pipes open at once in one session |
| `MAX_PIPES` | 8 | Pipes open at once in all sessions |
| `PIPE_CHUNK` | 32768 | Largest `pipe.data`, in bytes before base64 |
| `PIPE_QUEUE` | 256 | `pipe.data` messages from the hub that may wait for sshd, per pipe |

**`ansible.session`** takes exactly two parameters (anything else is refused, `unknown parameters:
…`):

| Field | Rule | Refusal |
| --- | --- | --- |
| `session` | `^[A-Za-z0-9_-]{8,64}$` | `session must be 8 to 64 letters, digits, - or _` |
| `minutes` | Whole number, 1 to `MAX_SESSION_MINUTES` | `minutes must be a whole number from 1 to 60` |

The agent records the session as open until now + `minutes` (a session id that is open already is
refused) and answers at once: `{"ok": true, "session": "<id>", "until": <Unix time>}`. The job does
not hold a worker while the session lasts.

**Pipes.** The hub sends `pipe.open {id, session}`, `id` matching `^[A-Za-z0-9_-]{1,64}$` (any other
id is ignored, there is nothing to answer). The agent refuses with `pipe.close {id, error}` when:

| Error | When |
| --- | --- |
| `no such session` | The session isn't open (never was, ended, or expired) |
| `the session is over` | Its time is up |
| `a pipe with this id is open already` | — |
| `the session has 4 pipes open already` | `MAX_PIPES_PER_SESSION` |
| `8 pipes are open already` | `MAX_PIPES` |
| `can't reach sshd on 127.0.0.1:<port>: …` | The TCP connection failed (5 s timeout) |

Otherwise it connects to **`127.0.0.1:<ssh_port>`** from `agent.conf`. No message carries an address
or a port, so a pipe can't reach anything else. Connecting MUST NOT hold up the link's read loop.
On success it sends `pipe.opened {id}`; the hub sends `pipe.data` only after that.

Bytes go both ways as `pipe.data {id, data}`, in order. The agent reads at most `PIPE_CHUNK` bytes
at a time from sshd and sends each read at once. A `pipe.data` from the hub that isn't base64 or is
larger than `PIPE_CHUNK` closes the pipe (`bad pipe.data`). The agent MUST NOT block the read loop
on a slow sshd: when `PIPE_QUEUE` messages wait already, it closes the pipe (`too much data waiting
for sshd`). `pipe.data` is never spooled: a pipe is live or gone.

A pipe ends when sshd closes the connection (`pipe.close {id}`, no error), the hub sends
`pipe.close {id}` (no answer), the hub sends `session.end {session}`, the session's time is up
(`the session's time is up`, checked at least every 5 s), or the link drops (no message; the hub
counts every pipe of a dropped link as closed). Sessions outlive a dropped link until their time
is up, so a run can go on over the next link with new pipes.

Audit lines: `session.open` (`session`, `until`, `port`), `session.end` (`session`, `reason`),
`pipe.open` (`id`, `session`), `pipe.close` (`id`, `session`, `up` and `down` in bytes, `reason`),
`pipe.refused` (`id`, `session`, `reason`). Never the bytes themselves.

The agent's user needs no sudo rule for this: it only opens a TCP connection to localhost. What
the run may do on the host is up to the SSH user labdex logs in as (`labdex-ansible`, set up by
`install.sh --ansible`).

## SSH keys

Go agent only. labdex manages who may log in over SSH, without ever editing a user's own
`~/.ssh/authorized_keys`: its keys go into root-owned files in `/etc/ssh/labdex`, which an sshd
drop-in (`/etc/ssh/sshd_config.d/05-labdex-keys.conf`, written by `install.sh --ssh-keys`) adds to
`AuthorizedKeysFile`:

| File | Holds |
| --- | --- |
| `/etc/ssh/labdex/<user>.keys` | labdex's keys for the user, one per line, under a comment that says edits are overwritten |
| `/etc/ssh/labdex/<user>.break-glass` | the host's break-glass key, for one user |

`hello` carries `ssh_users` (a list, `[]` when none). Both jobs run the agent's own program as root
through a fixed sudo line (the service's sandbox hides home folders, and only root writes
`/etc/ssh`). The root side reads `ssh_users` and `allow` from `agent.conf` itself, never from the
job.

**`ssh_keys.report`** takes no parameters. The result: `users` (root, every user from uid 1000 with a
login shell, and every user in `ssh_users`), each with `user`, `uid`, `managed` (in `ssh_users`),
`missing` (in `ssh_users`, no account), `own` (the files `~/.ssh/authorized_keys` and
`authorized_keys2`), `labdex` and `break_glass` (the two files above). A file is
`{path, keys: [{type, sha256, comment, bits?, options?}], other, error?}`: fingerprints, never
the keys, and lines that are no key are only counted. Files are opened without following a
symbolic link (a link gives `error`). Also `sshd` (from `sshd -T`: `authorizedkeysfile`,
`passwordauthentication`, `kbdinteractiveauthentication`, `permitrootlogin`,
`pubkeyauthentication`, `trustedusercakeys`, `authorizedprincipalsfile`), `sshd_error`,
`drop_in`, `uses` (sshd reads labdex's files), `exclusive` (and no others) and `at`.

**`ssh_keys.apply`** takes `users` (`{user: [key, …]}`, the whole wanted set: a user in `ssh_users`
that is left out gets an empty file) and optionally `break_glass` (`{user, key}`). The agent refuses
the job when a user isn't in `ssh_users`, when `ssh_users` is empty, when any key isn't a plain
public key, or when there are other parameters. A key is `<type> <base64> [comment]`:
`ssh-ed25519`, `ecdsa-sha2-nistp256/384/521`, `sk-ssh-ed25519@openssh.com`,
`sk-ecdsa-sha2-nistp256@openssh.com`, or `ssh-rsa` with 3072 bits or more; never options in front
(`command=`, `from=`, …); the comment up to 100 characters of `A-Za-z0-9 @._+:=/()-`; at most 200
keys per user. Then, as root:

1. `/etc/ssh/labdex` must be a real folder, root's, not writable by group or others (made `0755` if missing).
2. Each managed user's `<user>.keys` is replaced at once (a new file `0644` beside it, synced, renamed). Duplicates go.
3. A `*.keys` file of a user no longer in `ssh_users` is removed: those keys must stop working.
4. With `break_glass`, `<user>.break-glass` is written, and only then any other `*.break-glass` removed.
   Without it, the break-glass file stays: an apply never leaves a host without one it had.

The result: `ok`, `changes` (`[{user, keys, added: [sha256], removed: [sha256]}]`), `break_glass`
(`"set for <user>"` when it changed) and `report`, a fresh `ssh_keys.report`.

## The spool

What the agent has to say while the hub can't take it (results and heartbeats) goes to `buffer/`.

- One batch per file, named `NNNNNNNNNNNN.jsonl` (12-digit counter), one JSON record per line, the
  next counter after the highest existing file. Written to `.tmp`, synced and renamed.
  Leftover `.tmp` files are deleted at start.
- Limits: 64 MiB and 7 days. Past either, the oldest files go first, counted as dropped.
- A file that can't be read or parsed is dropped, so it can't block those behind it.
- After `welcome`, the agent sends the oldest file's records in order and deletes the file once
  every record was sent; it stops at the first send that fails. A crash halfway sends one batch
  twice at most; the hub ignores a second result for a job.
- A disk that can't be written drops the records and logs once until it works again.

## Certificates

When mTLS isn't off on the hub (`LABDEX_AGENT_MTLS`), labdex is a CA for its agents. The certificate
is `CN=<agent id>`, `O=labdex agent`, client auth only, EC P-256 or P-384. The hub checks it on the
link: issued by its CA, in date, CN is the agent id, and its SHA-256 is the current or previous one
on record. Behind a reverse proxy the hub reads it from `LABDEX_AGENT_CERT_HEADER`, only from
trusted proxies.

After `welcome`, the hub sends `renew` when the agent reported `certs: true` and its `cert_sha256`
isn't the latest on record, or the certificate runs out within 30 days. The agent answers with
`csr`; the hub answers `cert`; the agent saves it and uses it from the next link. The old
certificate keeps working until the agent links with the new one.

## Audit

`audit.log`: one JSON object per line, `ts` (Unix time, 3 decimals) and `event`:

| `event` | Fields |
| --- | --- |
| `refused` | `reason`, and `id`, `type` when known |
| `accepted` | `id`, `type`, `params`, `by` |
| `finished` | `id`, `type`, `ok`, `error` (300 chars), `seconds` |

Never a job's output. A write error MUST NOT stop the agent. The hub keeps its own audit (object
type `agent`).

## Command line

| Command | Does | Exit |
| --- | --- | --- |
| `labdex-agent enrol --url U --token T` | Enrol; `--token -` reads it from stdin; `LABDEX_AGENT_TOKEN` works too | 0, or non-zero with the reason |
| `labdex-agent run` | The service: link and run jobs until SIGTERM | 0 on SIGTERM; 2 not enrolled; 3 refused for good |
| `labdex-agent health` | Prints how long ago the hub answered | 0 within `HEALTHY_FOR`, else 1 |
| `labdex-agent sudoers [--user U]` | Prints the sudo rules | 0 |
| `labdex-agent show facts\|discovery\|patches\|metrics` | Runs a read-only collector here and prints JSON; sends nothing | 0 |
| `labdex-agent ssh-keys report\|apply` | As root only (through `ssh_keys_report` and `ssh_keys_apply`): the root side of [SSH keys](#ssh-keys) | 0, or 1 with the reason |
| `labdex-agent --version` | Prints the version | 0 |

Every command takes `--config` (default `/etc/labdex-agent/agent.conf`). Exit code 3 matters: the
systemd unit has `RestartPreventExitStatus=3`, so a revoked agent stays stopped.

## Install and service

`install.sh` (served at `/api/agents/install.sh`) installs the agent, its user, config, sudo rules
and systemd unit; `--uninstall [--purge]` removes them. The unit's hardening is part of the
contract: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/labdex-agent`, `ProtectHome`,
`PrivateTmp`, `ProtectControlGroups`, `CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_AUDIT_WRITE
CAP_DAC_READ_SEARCH CAP_SYS_PTRACE`, `UMask=0077`, `MemoryMax=256M`, `TasksMax=64`, and none of the
settings that imply `NoNewPrivileges` (sudo can't work under it).

## Conformance

`agent/testdata/conformance.json` holds cases both agents MUST pass: a signed job with a fixed test
key and every check failure, redaction, `ss`, `systemctl show`, Docker ports, cgroup units, apt and
dnf output. The Python tests (`backend/tests/test_agent_conformance.py`) and the Go tests read the
same file. A change to the contract changes this page and that file in the same pull request.

The end-to-end check is the install rehearsal, `backend/tools/agent_rehearsal.py`: a local labdex
over TLS with client certificates required, a systemd container standing in for a host, and 15
checks from install to `--uninstall --purge`. It installs the Go agent, like `install.sh`;
`--agent python` runs it with the Python agent.

## What may differ between agents

- `version` and the `python` / `agent` / `ssh_host_keys` facts.
- `ansible.session`, the `pipe.*` and `session.end` messages and `ssh_port`: the Go agent only. The
  Python agent stays as it is, and the hub uses them only with an agent whose `hello` lists
  `ansible.session`. The shared cases in `conformance.json` leave them out; the Go tests cover them.
- Log lines on stdout.
- Internal structure, as long as every MUST above holds.
