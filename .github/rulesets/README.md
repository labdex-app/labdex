# Repository rules and teams

Import the rulesets under Settings › Rules › Rulesets › New ruleset › **Import a ruleset**
(or once for every repository under the organization's Settings › Repository › Rulesets,
targeting the repositories by name).

| File | Where | What it does |
| --- | --- | --- |
| `labdex-branches.json` | labdex | `main` and `release/*`: pull requests only; one approval from a code owner who didn't push the last commit; new pushes dismiss approvals; open review threads block; **Lint** and **CI passed** green on pull requests (not on creating a branch: `release/X.Y` starts from a commit on `main`, which has no pull-request checks); no force-push, no deletion |
| `plugin-branches.json` | every `labdex-plugin-*`, `labdex-plugins` | the same without required checks (they have no pull-request CI) |
| `release-tags.json` | every repository | only organization admins create `v*` tags (a release); nobody moves or deletes one |

Organization admins may merge a pull request without the approval (bypass "for pull requests
only"): while there's one maintainer, that's how your own pull requests get in. Nobody,
admins included, pushes to `main` or `release/*` directly.

**CI passed** is the last job of `ci.yml`: it waits for every other job and fails if one
failed. Jobs a change doesn't need are skipped, so they can't be required one by one.
Replace the older "Main" ruleset of labdex with `labdex-branches.json`.

## Teams

`CODEOWNERS` routes reviews to these teams of labdex-app; create them (Organization ›
Teams) before importing the rules, or code owner review can't be satisfied:

| Team | Repository role | Reviews |
| --- | --- | --- |
| `maintainers` | Maintain on every repository | everything |
| `security` | Write on labdex | keys, signing, sign-in, the plugin sandbox, the release workflow |
| `plugin-maintainers` | Write on the `labdex-plugin-*` repositories, Maintain on labdex-plugins | the plugins and their pointers in labdex |
| `docs` | Write on labdex | `docs/` |
| `triage` | Triage on every repository | (labels, closing issues; no code) |

Give people the least role that lets them do their part, and keep organization owners to two
or three. Members should have two-factor authentication on (Organization › Settings ›
Authentication security › Require two-factor authentication).
