# Patching (Ansible)

A [labdex](https://github.com/labdex-app/labdex) plugin that installs the waiting updates on the
hosts of a site with Ansible, after a person approved the plan, reboots where needed if allowed,
and reports every host's patch state back.

- **Install updates** (plan): one line per host with updates waiting, from its last patch report;
  every run needs an approval. Upgrades with apt, dnf or yum, one host at a time by default,
  stops at the first failure.
- **Read patch state** (run): `labdex-patch-report --dry-run` over SSH, nothing installed.

Documentation: *Plugins › Patching (Ansible)* in the labdex docs.

Licence: AGPL-3.0 (see LICENSE and NOTICE).
