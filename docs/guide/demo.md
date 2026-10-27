# Demo mode

`LABDEX_DEMO=1` turns an instance into a public playground.

```sh
docker run -d -p 8080:8080 -e LABDEX_DEMO=1 ghcr.io/reykimedev/labdex:1
```

- On an empty database, and every night at 03:00 UTC, everything is **wiped** and the
  sample lab is loaded: networks, hosts, services, aliases and a zone policy
  (`examples/homelab.yaml`).
- Sign in as `demo` / `demo-labdex`; the sign-in page says so, and a banner says when
  the next reset is.
- The built-in *Demo lab* plugin (only there in demo mode) is a pretend DNS server and
  monitor: plan a DNS sync, approve it, watch it apply; one host has a bad day; the
  dashboard and host pages get widgets.
- Nothing reaches a network. Only *Demo lab* and *File exports* can have instances;
  plugins can't make HTTP requests or run Ansible; installing plugins, repositories,
  notification channels, secrets, restores and changes to users and passwords are off.

!!! danger
    Demo mode deletes everything in its database every night. Point it at its own data
    folder.
