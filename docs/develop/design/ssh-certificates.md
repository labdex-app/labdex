# Design: SSH certificates (later)

Status: **proposal**, nothing built. Step 1, ordinary keys managed through the agent, is
[SSH keys through the agent](../../guide/ssh-keys.md). This page is the plan for step 2, so the
first step doesn't close any doors.

## 1. Goal

People log in with short-lived SSH certificates instead of keys that sit in files on every host.
labdex shows who may get which certificate and where they work. A small command line tool asks
for one.

## 2. The rule that shapes it

**The certificate authority does not live in labdex.** labdex is reachable from the hosts it
manages and holds the agent job-signing key, so a CA inside it would put every host's logins
behind one more thing to break into. The CA runs on its own private host:

- not reachable from the internet, and not a host public nodes can reach;
- its signing key never leaves it (TODO: a file with a passphrase, a TPM, or a hardware key; to decide);
- it signs only what an approved request in labdex says, and keeps its own log.

Public VPS nodes keep reaching labdex only through their agent's outbound link. Nothing about the
CA needs a new way into them: the agent puts the CA's **public** key on the host, like a key in
step 1.

labdex already has one SSH CA of its own, for its Ansible runs through the agent (per run and host,
at most an hour, only from `127.0.0.1`; see [Ansible through the agent](ansible-through-the-agent.md)).
That one stays as it is and stays separate: it signs labdex's own logins, never people's.

## 3. Parts

| Part | Where | Does |
| --- | --- | --- |
| Certificate page | labdex | Who may get a certificate, for which principals, how long; the requests and what was issued (serials, never keys) |
| Signer | the CA host | Polls labdex for approved requests (outbound only), signs, posts the certificate back |
| CLI | people's machines | `TODO-name request` makes or uses a local key, sends the public key to labdex, waits for the certificate, writes it beside the key |
| Host side | the Go agent | Puts the CA public key in `TrustedUserCAKeys` and the principals per user in `AuthorizedPrincipalsFile` (a new write job under `ssh_keys`), and reports them in `ssh_keys.report` (which already reads `trustedusercakeys` and `authorizedprincipalsfile` from `sshd -T`) |

## 4. Open questions for Mike

- Validity: how short (TODO: hours? a working day?).
- Approval: every request by an administrator, or rules that sign without a person (TODO).
- Revocation: a revoked-keys list pushed by the agent, or only short lifetimes (TODO).
- The CA host: which machine, how it is backed up, how its key is rotated (TODO).
- Whether the break-glass key of step 1 stays a plain key (suggested: yes, so it works without the CA).

## 5. How step 1 prepares it

- `/etc/ssh/labdex` and the sshd drop-in are already labdex's own place on the host; step 2 adds
  files there instead of touching the users' files.
- The report already shows `trustedusercakeys` and `authorizedprincipalsfile`.
- `ssh_users` already limits which users labdex may touch; principals files would follow it.
