# Notifications

Channels under Admin → Notifications: **ntfy** (server, topic, token, priority),
**Matrix** (homeserver, room, access token), **email** (SMTP with STARTTLS or SSL) and
**webhooks** (JSON; with a secret, an `X-Labdex-Signature: sha256=…` HMAC of the body).
Each channel picks its events and, optionally, the integrations it hears about.

| Event | When |
| --- | --- |
| `plan.waiting` | a plan needs approval |
| `plan.applied` | a plan was applied |
| `job.failed` | a job failed |
| `backup.failed` | a backup failed |
| `host.down`, `host.up` | a monitoring source changed a host's status |
| `policy.updated` | the zone matrix changed |
| `plugin.<id>.<event>` | anything a plugin sends with `ctx.notify` |
| `test` | the Test button |

Messages go out from the worker through an outbox; failed ones are retried after 1,
5, 15 and 60 minutes. Set *Address of this labdex* (Admin → General) so messages link
back to the job.
