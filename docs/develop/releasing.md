# Branches, versions and releases

labdex uses **release branches**: `main` is where the next version grows, a branch per
minor version is where it's stabilised and later patched, and every release is a GitHub
release on that branch.

```
main ──●──●──●──●──────●──●──●──●──────────●──●──▶  (next: 1.25)
          \                 \
           release/1.23      release/1.24 ──●──●──▶
            v1.23.0  v1.23.1   v1.24.0   v1.24.0-1
```

## Versions

`MAJOR.MINOR.SUB`, plus `-N` for a hotfix:

| Change | Version | Branch |
| --- | --- | --- |
| New features (a release) | `1.24.0` | `release/1.24`, cut from `main` |
| Small additions, docs, CI, no migration | `1.24.1` | `release/1.24` |
| Bug fixes only | `1.24.0-1`, `1.24.0-2` | `release/1.24` |
| Breaking changes (a database or API that older clients can't use) | `2.0.0` | `release/2.0` |

A hotfix sorts *after* its release (Python calls 1.24.0-1 a post-release), so plugin ranges
like `labdex: ">=1.24,<2"` include it. Its image takes the floating tags (`1.24.0`, `1.24`,
`1`, `latest`): installs that follow `1.24` get the fix.

Plugins are versioned on their own (`version` in `plugin.yaml`, `MAJOR.MINOR`) and released
from their own repositories; a plugin fix never waits for labdex.

## Branches

| Branch | For | Protected |
| --- | --- | --- |
| `main` | the next minor version; always green | yes: pull requests with green CI only |
| `release/X.Y` | X.Y.0 and everything after it (`X.Y.1`, `X.Y.0-1` …) | yes: pull requests only |
| `feature/<topic>`, `fix/<topic>` | one change; short-lived | no |
| `dependabot/…` | dependency updates, weekly | no |

Every change reaches `main` and `release/*` through a pull request. Pushes run the quick
lint; pull requests run the full CI (tests on SQLite and PostgreSQL, browser tests, the
performance budget, the image and the docs).

## Making a release

1. **Cut the branch** when `main` has what the release should have:
   `git switch -c release/1.24 main`, then `python backend/tools/bump.py minor` (writes
   1.24.0 everywhere and opens its CHANGELOG heading), fill in the CHANGELOG, and open a pull
   request into `release/1.24`. Nothing new goes in from here on: fixes only.
2. **Plugins first.** If the release needs new plugin versions, merge and release those in
   their repositories (they're then in the Marketplace) and point the submodules at them in
   the same pull request.
3. **Release.** When CI is green and the pull request is merged, publish a GitHub release on
   `release/1.24` tagged `v1.24.0` (`gh release create v1.24.0 --target release/1.24 --notes ""`).
   The release workflow checks the tag against the code, builds the image on amd64 and arm64,
   signs everything, attaches the files and the CHANGELOG section, and publishes this
   version's docs.
4. **Back to `main`.** Merge `release/1.24` into `main` (a pull request), so the version,
   the CHANGELOG and any fixes made on the branch are there too. Then `bump.py minor` on
   `main` starts 1.25.0.

## Fixing a release

1. Branch from the release branch: `git switch -c fix/<topic> release/1.24`.
2. Fix it, with a test. `python backend/tools/bump.py patch` (1.24.0 → 1.24.0-1) and a
   CHANGELOG entry.
3. Pull request into `release/1.24`; once merged, publish `v1.24.0-1` on `release/1.24`.
4. Pull request `release/1.24` → `main` (or cherry-pick the fix), so the next version has it.

Only the newest release branch gets hotfixes, unless a fix is about security: then the
previous one too.

## What each release has

- a GitHub release with the CHANGELOG section, the source (with the plugins), the wheel,
  their SBOMs and cosign bundles
- `ghcr.io/reykimedev/labdex:<version>` for amd64 and arm64, signed, with its SBOM attached
- its own docs: `https://reykimedev.github.io/labdex/<X.Y>/` (the version picker at the top);
  `latest` is the newest release, `dev` is `main`

## Checklist

- [ ] CHANGELOG: what changed, for the people running it (not the commits)
- [ ] Upgrading from the previous release works (`tests/test_upgrade_gate.py` and the
      migration tests)
- [ ] Docs describe the new behaviour; screenshots are current (`docs/screenshots.py`)
- [ ] New plugin versions are released and the submodules point at them
- [ ] CI is green on the pull request
