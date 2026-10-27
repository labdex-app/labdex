# Security policy

labdex keeps an inventory, firewall and DNS automation and a secrets vault, so security
problems matter, even in a home lab. It's built largely with an AI coding assistant and has
had no independent audit: please run it on a trusted network, behind your own access
controls, and never on the internet (see [Security](docs/operate/security.md)).

## Supported versions

Security fixes go into the newest release branch, as a hotfix (`1.24.0-1`), and into the one
before it when the problem is serious. Older versions don't get fixes: upgrade.

| Version | Supported |
| --- | --- |
| newest minor (1.24.x) | yes |
| the one before (1.23.x) | serious problems only |
| older | no |

Plugins are fixed in their own repositories (`labdex-plugin-<id>`) and reach installations
through the Marketplace, signed with the project key.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately on GitHub: the repository's
*Security* tab › *Report a vulnerability*
(<https://github.com/labdex-app/labdex/security/advisories/new>), for a plugin in that
plugin's repository.

Please include the version, what an attacker needs (network access, a user account and
which rights, a malicious plugin …), what they get, and the steps to reproduce it.

What happens then:

* an acknowledgement within a week (this is a spare-time project)
* an assessment, and a fix in a hotfix release when the problem is confirmed; you're kept
  up to date in the advisory
* a published advisory (GHSA, a CVE when it's worth one) after the fix is out, crediting you
  unless you'd rather not be named

What's not a vulnerability in labdex: problems that need administrator rights (an
administrator can install unsigned plugins and run code by design), problems in the systems
labdex talks to, and running labdex exposed to the internet against the documented advice.
