# Pulumi (public site)

A [labdex](https://github.com/labdex-app/labdex) plugin. Keeps the hosts of a site in existence at
cloud providers with Pulumi: a Contabo instance per node and a Cloudflare A/AAAA record per name,
made from the inventory. Every change is previewed as a plan and applied after review. Pulumi's
state stays in labdex's data folder, encrypted with your passphrase; no Pulumi Cloud account is
needed.

**Version** 0.1 · **Works with** labdex >=1.24 (with sites) · **Plugin id** `pulumi`

> [!WARNING]
> Built with AI, for home labs only: labdex and its plugins were written largely with an AI
> coding assistant (Claude), directed and reviewed by a human. They haven't had an independent
> security audit and may contain security problems. Run them on a trusted home network.
> This first version has been tested against fakes, not yet against real Contabo and Cloudflare
> accounts.

The engine itself (resources, the Automation API wrapper, the Contabo provider) is part of
labdex as `labdex.iac`; this plugin is the settings, actions and host panel on top of it.

Needs labdex built with `--build-arg PULUMI=1` (the Pulumi SDK, the Cloudflare provider and the
CLI). See *Pulumi (public site)* in the labdex docs for settings, adopting existing nodes and
the safety rules.
