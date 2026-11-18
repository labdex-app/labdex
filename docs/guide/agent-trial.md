# A first agent, read-only

Try the [agent](agent.md) on one host before you roll it out. Use a VM that nothing depends on,
and leave the writes off: the agent then only reads, and you can take it all back with one command.

## Before you start

- **The host:** Debian 12 or newer, Ubuntu 22.04 or newer, Fedora or RHEL 9, with systemd and
  Python 3.11 or newer (`python3 --version`). A VM or a container with systemd will do. Take a
  snapshot first if it is a VM.
- **labdex:** reachable from the host over `https://`, with this host in the inventory. If its
  certificate is from your own CA, copy that CA's certificate to the host for `--ca-file`.
- **A reverse proxy in front of labdex** must pass WebSockets through for `/api/agents/link`.
  For client certificates, set it up as in [Client certificates](agent.md#client-certificates)
  first, or leave `LABDEX_AGENT_MTLS` at `optional` for the trial.
- **Outgoing only:** the host opens one connection to labdex. Open no port on the host.

## Install

1. In labdex, open the host, then the **Agent** tab, and choose **Enrol an agent**. Leave *run it
   as root* unticked. Copy the command; its token works once, for an hour.
2. On the host, download `install.sh` and read it. Then run the command, without `--allow` and
   without `--root`:

    ```sh
    curl -fsSLo install.sh https://lantern.example.net/api/agents/install.sh
    less install.sh
    sudo sh install.sh --url https://lantern.example.net --token lxe_… --sha256 …
    ```

3. It ends with `labdex agent installed and running as labdex-agent`.

## Check it

On the host:

```sh
systemctl status labdex-agent        # active (running)
labdex-agent health                  # "the hub answered … s ago"
sudo cat /etc/sudoers.d/labdex-agent # only ss and docker/podman ps: no apt, no shutdown
journalctl -u labdex-agent -n 20
```

In labdex, on the host's **Agent** tab:

- the agent is *linked* and *runs as* `labdex-agent`. Under the settings, every capability says
  *host doesn't allow*;
- under *certificate*, it says *used on this link* (if your proxy passes certificates);
- choose **Ping**, then **Facts**, then **Discover services**. Each one shows up under the jobs,
  and the discovery tables fill in. The ports also appear on the **Discovery** page (source `agent`);
- after a minute, the metrics show CPU, memory and `/`;
- **History** for the host lists the token, the enrolment and each job.

## What it changed on the host

| | |
| --- | --- |
| `/opt/labdex-agent/labdex-agent.pyz` | the agent |
| `/usr/local/bin/labdex-agent` | a two-line wrapper to run it |
| `/etc/labdex-agent/agent.conf` | the hub's URL, writes allowed (none) |
| `/etc/sudoers.d/labdex-agent` | the read-only sudo rules |
| `/etc/systemd/system/labdex-agent.service` | the service |
| `/var/lib/labdex-agent/` | key, certificate, audit log, offline buffer (mode 0700) |
| user and group `labdex-agent` | no shell, no home |

Packages are installed only if `python3`, its `cryptography` package or `sudo` were missing; the
script says which.

## Take it back

```sh
sudo sh install.sh --uninstall --purge
```

This stops and removes all of the above. Then choose **Revoke** on the host's Agent tab, so the key
and certificate are dead in labdex as well. Revoking first works too: the agent stops at once and
stays stopped until you uninstall it.

## When something is wrong

| You see | Look at |
| --- | --- |
| `enrolment refused (403): unknown, used or expired enrolment token` | make a new token: each works once, for an hour |
| `the hub URL must be https://` | the URL in the command; labdex needs HTTPS for agents |
| the agent logs `no link to …` and keeps trying | the reverse proxy: WebSockets for `/api/agents/link` |
| `the hub wants a valid client certificate` | the proxy doesn't pass the certificate, while `LABDEX_AGENT_MTLS` is `required` |
| the service stopped with status 3 | labdex refused the agent (revoked, or root not allowed); `journalctl -u labdex-agent` says which |
| discovery notes `the sudo rule for ss is missing` | `sudo cat /etc/sudoers.d/labdex-agent`, and that the service has no `NoNewPrivileges` (see [What runs with privileges](agent.md#what-runs-with-privileges)) |
