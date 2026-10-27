# Ansible and features

Features are a tree (`server` → `server.packages` → `server.packages.pve`) with
variables and playbooks or roles. Hosts get features; variables merge root to leaf,
then the host's own variables. Making `kube.cluster.oak` makes `kube` and `kube.cluster`
too when they don't exist yet (empty, ready for their own variables), in the UI, the API
and a YAML import.

```yaml
features:
  - {path: server, vars: {ntp: 10.10.3.253, pkgs: [vim, curl]}}
  - {path: server.packages, playbooks: [playbooks/packages.yml]}
  - {path: kube, vars: {version: v1.30.6}, roles: [k3s]}
  - {path: kube.cluster.oak, vars: {version: v1.31.2, vip: 10.10.128.10}}
hosts:
  - name: srv-vm-k3s-01
    features: [server.packages, kube.cluster.oak]
    vars: {k3s_role: server}
```

The **Ansible runner** applies features from a folder or a git repository: playbooks,
then roles, ancestors first, each limited to the hosts that have the feature.
`--check --diff` is the plan, the real run happens on apply. Per host it can apply
the host's features, run a command or install packages.

Variables can point at a secret instead of holding it: `{$secret: grafana-admin}`.
The value is filled in only while Ansible runs and is masked in logs. Files encrypted
with `ansible-vault` work with the vault password setting; see
[Templates and Ansible Vault](../operate/templates.md).

The same variables and `feature_<path>` groups are in the Ansible inventory export,
so your own `ansible-playbook` runs see them too.
