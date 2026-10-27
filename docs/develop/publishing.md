# Publishing plugins

## Sign and pack

```sh
labdex plugin keygen my.pem                      # my.pem (keep it secret) and my.pem.pub
labdex plugin sign ./pihole --key my.pem --signer "Your Name"
labdex plugin pack ./pihole -o pihole-0.1.zip
```

People trust your key under Plugins → Signing keys (paste `my.pem.pub`); then your
plugin installs as *community*.

## A plugin in its own git repository

That's how labdex's own plugins live (`labdex-plugin-<id>`): the plugin at the root of a
repository, released by publishing a GitHub release. Start one from a plugin folder:

```sh
python3 backend/tools/plugin_repo/make_plugin_repo.py ./pihole ../labdex-plugin-pihole --community
```

It adds a README from the manifest, a licence (MIT for your own plugins, change it as you like; AGPL-3.0 for official ones), a CHANGELOG and the release workflow.
Set the repository secret `LABDEX_SIGNING_KEY` to your private key. Then each release is:
bump `version` in `plugin.yaml`, add a `## <version>` section to `CHANGELOG.md`, commit,
then publish a GitHub release tagged `v<version>` (in the web UI, or
`gh release create v<version> --target main --notes ""`). The workflow checks the tag against
the manifest, signs exactly what's committed and attaches `<id>-<version>.zip` to the
release with `CHANGELOG.md`; the release description gets that version's CHANGELOG section
(below anything you wrote). Files listed in `.releaseignore` (one pattern per line, like
`.gitignore`: `tests/`, `*.log`, `docs/draft.md`) stay out of the zip; so do the repository's
own files (`.git`, `.github`, `.gitignore`). `labdex plugin prune <copy>` does that by hand.

## A Marketplace from plugin repositories

A Marketplace is a repository with a `plugins.yaml`:

```yaml
name: My plugins
description: Plugins by me
plugins:
  - repo: me/labdex-plugin-pihole
    withdrawn: {"0.3": "loses the blocklists on restart"}
```

Add `discover: [{owner: me, prefix: labdex-plugin-}]` and every public repository of
yours named like that is included too, so a new plugin repository shows up by itself.

`labdex plugin build-index plugins.yaml site --key my.pem` reads every listed repository's
releases (with `GITHUB_TOKEN` set it can make more API calls), checks each archive's
checksum and that it's signed with *your* key, and writes a signed `site/index.json` that
points at the release assets. Every release is a version people can install, update to or
go back to, with its notes; a `withdrawn` version stays installable on purpose but warns
everyone who has it, and updates skip it. Publish `site/` on GitHub Pages: the
[labdex-plugins](https://github.com/reykimedev/labdex-plugins) repository is a ready-made
example (it rebuilds every hour, when `plugins.yaml` changes, and on a
`plugin-released` dispatch from a plugin's release workflow).

## A repository from plugin folders

Or put each plugin in its own folder and build a repository with the archives in it:

```sh
labdex plugin repo ./plugins ./public --key my.pem --name "My plugins" --description "…"
```

This signs every plugin, zips them and writes a signed `public/index.json`. Publish
`public/` on any web server (GitHub Pages works). People add the URL of `index.json`
under Plugins → Repositories, compare the key id with the one you publish, and install
from the marketplace. The key is pinned: changing it means people add the repository
again.

Each index entry carries the plugin's `labdex` range, so the marketplace only offers
plugins that work with the labdex looking at it. Building into a folder that already has
an index keeps the older versions whose archives are still there, so people can go back
to them; `labdex plugin fetch-repo <url> <folder> --key my.pem` downloads what's published
first. A plugin folder's `CHANGELOG.md` (`## <version>` sections) gives each version its notes.

Withdraw a version (or undo it) and re-sign the index:

```sh
labdex plugin yank ./public pihole 0.3 --key my.pem --reason "loses the blocklists on restart"
labdex plugin yank ./public pihole 0.3 --key my.pem          # offered again
```

## The official Marketplace

Every official plugin lives in its own repository (`reykimedev/labdex-plugin-<id>`); labdex
has them as git submodules in `plugins/` for its tests, but doesn't ship them: users install
them from the Marketplace. [labdex-plugins](https://github.com/reykimedev/labdex-plugins) lists them; its
index is signed with the project key and published at
`https://reykimedev.github.io/labdex-plugins/index.json`, which labdex trusts out of the
box. Contributions are welcome: see [Contributing](contributing.md).

## labdex's own versions

labdex releases are `MAJOR.MINOR.SUB`; a hotfix of a release is `MAJOR.MINOR.SUB-N`:

| Version | What it is |
| --- | --- |
| 1.9.0 | a release |
| 1.9.0-1, 1.9.0-2 | its hotfixes: bug fixes only, they come *after* 1.9.0 |
| 1.9.1 | a small release |
| 1.10.0 | the next release |

A hotfix is what Python calls a post-release (1.9.0-1 is 1.9.0.post1), so plugin ranges
like `labdex: ">=1.9,<2"` take hotfixes as you'd expect. The image of 1.9.0-1 is tagged
`1.9.0-1`, `1.9.0`, `1.9`, `1` and `latest`: a hotfix replaces its release.

To make one, `python backend/tools/bump.py sub|minor|major|patch` (or an exact version)
writes the version everywhere and opens a changelog heading. Push that to `main`, then
publish a GitHub release tagged `v<version>` on it (`gh release create v1.23.0 --target main
--notes ""`): the release workflow builds and signs the image and the files and attaches
them, with the changelog section as notes if you left them empty. It refuses a tag that
doesn't match the code's version.
