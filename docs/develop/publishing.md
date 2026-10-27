# Publishing plugins

## Sign and pack

```sh
labdex plugin keygen my.pem                      # my.pem (keep it secret) and my.pem.pub
labdex plugin sign ./pihole --key my.pem --signer "Your Name"
labdex plugin pack ./pihole -o pihole-0.1.zip
```

People trust your key under Plugins → Signing keys (paste `my.pem.pub`); then your
plugin installs as *community*.

## A repository

Put each plugin in its own folder and build a repository:

```sh
labdex plugin repo ./plugins ./public --key my.pem --name "My plugins" --description "…"
```

This signs every plugin, zips them and writes a signed `public/index.json`. Publish
`public/` on any web server (GitHub Pages works). People add the URL of `index.json`
under Plugins → Repositories, compare the key id with the one you publish, and install
from the marketplace. The key is pinned: changing it means people add the repository
again.

Each index entry carries the plugin's `labdex` range, so the marketplace only offers
plugins that work with the labdex looking at it.

## The official repository

The official repository is built from `plugins/` in the labdex repository by the docs
workflow, signed with the project key (a CI secret), and published with this site
under `/plugins/`. labdex trusts it out of the box. Contributions are welcome: see
[Contributing](contributing.md).

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
writes the version everywhere and opens a changelog heading. The release workflow
refuses a git tag that doesn't match the code's version.
