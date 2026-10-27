# Monitoring

## VictoriaMetrics / Prometheus

Reads `up` from a VictoriaMetrics or Prometheus API: a host is down when every target
that belongs to it is down. CPU and memory widgets on host pages.

## Beszel

Host status, CPU, memory and disk from Beszel agents. Adds hosts as systems (plan/apply)
and installs the agent over Ansible.

## Uptime Kuma

Plans a monitor per service that has a link and reads host and service status back.
Needs the `uptimekuma` extra (in the image).

## Certificates

Checks the TLS certificate of every https service (and endpoints you list): days left,
issuer, names; warns before expiry and notifies.

Status from any of these shows as live dots on Hosts and in the sidebar, on the
Services page, and as `host.down` / `host.up` notifications.
