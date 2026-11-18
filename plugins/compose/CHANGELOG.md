# Compose stacks

## 0.1
- First version: Compose stacks kept in labdex (compose file and environment, secrets as vault
  references), planned against the stack folders on the Docker hosts, deployed with Ansible and
  `docker compose up -d` after an approval. Only folders labdex wrote are changed; removing
  stacks is off unless you turn it on.
