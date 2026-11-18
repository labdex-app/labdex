# Compose stacks

A [labdex](https://github.com/labdex-app/labdex) plugin that keeps Docker Compose stacks as code:
each stack's compose file and environment live in labdex, are planned against what the Docker
host has, and are deployed with Ansible (`docker compose up -d`) after an approval.

Documentation: *Plugins › Compose stacks* in the labdex docs.

Licence: AGPL-3.0 (see LICENSE and NOTICE).
