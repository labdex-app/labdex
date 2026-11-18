# NetBird

A [labdex](https://github.com/labdex-app/labdex) plugin. Peers of a NetBird network (netbird.io or self-hosted) into Discovery with their mesh addresses, so they can be assigned to hosts; known peers report connected or not as host status, with their groups, OS and version on the host page.

**Version** 0.1 · **Works with** labdex >=1.24 · **Plugin id** `netbird`

> [!WARNING]
> Built with AI, for home labs only: labdex and its plugins were written largely with an AI
> coding assistant (Claude), directed and reviewed by a human. They haven't had an independent
> security audit and may contain security problems. Run them on a trusted home network.

It is read-only: it lists peers (`GET /api/peers`) and changes nothing in NetBird.

## Setup

1. In NetBird, open **Team › Service users** and create a service user with the read-only role.
2. Create a personal access token for it.
3. In labdex, add the NetBird integration in the site the peers belong to and paste the token.
   Self-hosted: set the management URL (e.g. `https://netbird.example.net`).
4. Run **Read peers**, then assign the unknown mesh addresses in Discovery, e.g. to a network of
   your remote-access area.

## Settings

| Setting | Notes |
| --- | --- |
| Management API | `https://api.netbird.io`, or your self-hosted management URL |
| Access token | A personal access token of a read-only service user |
| A disconnected peer is | Laptops and phones disconnect all the time; 'down' sends host.down |
| Only peers in these groups | Group names; empty = every peer |

## Actions

- **Check connection** (run)
- **Read peers** (run, schedulable)
