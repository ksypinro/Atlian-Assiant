# Security policy

Atlian-Assiant handles Atlassian API tokens, OAuth credentials and webhook secrets, so security reports are very welcome.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on <https://github.com/ksypinro/Atlian-Assiant/security/advisories/new>.

Include what you found, how to reproduce it and which version you used. You will get an answer within a few days; fixes are released as
soon as they are ready and credited in the advisory unless you prefer otherwise.

## Supported versions

The project is in alpha; only the latest release receives security fixes.

## Design notes for users

* The client is **read-only by default**; writes need explicit `access` and destructive actions always ask for confirmation.
* Credentials are read from the environment or a secret provider and are never logged; CLI backends run third-party tools with a clean
  environment (no tokens passed through).
* The browser and direct-database backends are off unless explicitly enabled (`allow_browser_writes`, `allow_direct_db`).
* Never commit your `.env` file. Use scoped API tokens with the smallest set of scopes you need.
