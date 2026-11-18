# Patching (Ansible)

## 0.1
- First version: a patch job (apt, dnf, yum) over the hosts of a site, planned from the hosts'
  patch state, applied only after an approval, one host at a time by default, with an optional
  reboot where one is needed; every host it reaches reports its state back. A report-only action
  reads the patch state over SSH without the agent on the host.
