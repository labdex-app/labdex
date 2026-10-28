Contributing to labdex
======================

Thanks for considering an issue or a pull request. Below are a few hints to make them as
useful as possible.

labdex is a home lab tool, built largely with an AI coding assistant and maintained by one
person in their spare time: answers can take a while, and not every idea will make it in.

Issue reports
-------------

Issues can be bug reports, feature requests or questions. Please search the open and closed
issues first; joining a discussion that's already there is often better than a new issue.

For a bug, please give:

* the labdex version where it happens (Help › *For a bug report* › *Copy* has it, with the database and
  the plugins), and the last version where it didn't, if you know it
* the page (its address without your host name) or the API call involved
* the steps to make it happen again, and what you expected instead
* for a plugin: the plugin and its version, and the system it talks to (OPNsense 26.1 …)

The issue templates ask for exactly that. Never paste secrets, API keys, backups or your
inventory into an issue; anonymise names and addresses that matter to you.

Labels:

* **bug**: something that worked as documented doesn't
* **feature**: new behaviour, an addition or a missing option
* **cleanup**: cosmetic or not operational (layout, wording, docs)
* **plugin**: belongs to one of the `labdex-plugin-*` repositories (it may be moved there)
* **upstream**: the problem is in a system labdex talks to, or in a dependency
* **help wanted**: a good idea nobody is working on: a pull request is welcome
* **incomplete**: the template's questions aren't answered yet

Ideas that fit what labdex is for (a small, self-hosted inventory that keeps a home lab's
firewall, DNS and monitoring in step) land on the roadmap; things beyond that are often
better as a [plugin](docs/develop/plugins.md) in a repository of your own. Issues without
activity for 180 days are closed. Stay kind and open. :)

Security problems
-----------------

Not in an issue: see [SECURITY.md](SECURITY.md).

Pull requests
-------------

* Base your work on the latest `main` (or, for a fix to a release, on its `release/X.Y`
  branch); see [Branches, versions and releases](docs/develop/releasing.md)
* One topic per pull request; explain the problem and your solution
* Cite issues: `Fixes: #123`, `Closes: #123` or `Ref: #123`
* Add or change tests with your change, and run them (below); CI runs them all again. Name new test files after the feature, not the release (see `backend/tests/README.md`)
* Keep the UI's voice: plain words, sentence case, what something does rather than how it's built
* Update the docs and the CHANGELOG (under the next version) when behaviour changes
* Database changes need a migration and must upgrade an existing database
  (`tests/test_upgrade_gate.py`)
* Code review may ask for changes; it's there to help the proposal land

Running it
----------

```sh
cd backend && pip install -e ".[server,postgres,uptimekuma,test]" ruff
pytest -q                                                       # SQLite
LABDEX_TEST_DATABASE_URL=postgresql://u@localhost/t pytest -q   # the same suite on PostgreSQL
LABDEX_E2E=1 pytest -q tests/e2e                                # browser tests (build the UI first)
ruff check labdex tools tests
cd ../frontend && npm ci && npm run lint && npm run dev         # Vite on :5173, proxies /api
```

The official plugins (for the tests; they aren't part of the package) are git submodules: `git clone --recurse-submodules`, or
`git submodule update --init` in a clone you have.

Licence of contributions
------------------------

labdex is under the [GNU Affero General Public License v3.0](LICENSE) with the plugin
exception in [NOTICE](NOTICE). By opening a pull request you agree that your contribution
is published under the same terms (inbound = outbound), and you confirm that it is yours to
give and contains nothing under a licence that isn't compatible with the AGPL. Sign your
commits off (`git commit -s`) to say so: that's the
[Developer Certificate of Origin](https://developercertificate.org/).
