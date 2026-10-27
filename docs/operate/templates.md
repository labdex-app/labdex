# Job templates and Ansible Vault

## Templates

A template is an action with its parameters (and host, for host actions) saved under
a name on the integration's page. Run it with one click, or on a schedule: every N
minutes, or on chosen days at a time in a time zone. Scheduled plans go through
approvals like any other. A schedule runs unattended, so it needs admin rights on the
integration.

## Secrets in variables

Feature and host variables can point at a field of a [vault](../guide/vault.md) entry:

```yaml
vars:
  admin_password: {$secret: vault:apps/grafana#admin}
  db_password: {$secret: grafana-db}   # older form: vault:global/grafana-db#value
```

Pointing at a secret for the first time needs `secret:apply` on it (the right to reveal it);
what someone else pointed at stays when you edit. A run needs the vault unsealed.

The value is filled in only while labdex runs Ansible (in a private temporary folder)
and never reaches the inventory, the YAML export, the API or History. Secret values are
masked as `********` in job logs, `--diff` included. Lint reports references to secrets
that don't exist.

## Ansible Vault

- The Ansible runner takes a vault password (and optional vault id) for files in your
  playbooks encrypted with `ansible-vault`.
- File exports: with a vault password, the Ansible inventory export writes secrets as
  `!vault` values, so playbook runs outside labdex decrypt them with the same password.
