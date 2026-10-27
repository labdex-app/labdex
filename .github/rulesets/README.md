# Repository rules

Import these under Settings › Rules › Rulesets › New ruleset › **Import a ruleset**.

| File | Where | What it does |
| --- | --- | --- |
| `labdex-branches.json` | labdex | `main` and `release/*`: pull requests only (no approval needed), the checks **Lint** and **CI passed** must be green, no force-push, no deletion |
| `plugin-branches.json` | every `labdex-plugin-*`, `labdex-plugins` | the same without required checks (they have no pull-request CI) |
| `release-tags.json` | every repository | `v*` tags can't be moved or deleted once pushed |

**CI passed** is the last job of `ci.yml`: it waits for every other job and fails if one
failed. Jobs a change doesn't need are skipped, so they can't be required one by one.
Replace the older "Main" ruleset of labdex with `labdex-branches.json`.
