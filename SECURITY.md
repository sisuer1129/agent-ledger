# Security policy

## Reporting a vulnerability

When GitHub Private Vulnerability Reporting is enabled for this repository, use it for security reports. Do not post sensitive findings in a public issue. This repository does not publish a security contact email.

If private reporting is not enabled, open a minimal public issue only when it can be described safely without revealing an exploit, deployment details, or financial data. The maintainer may then enable a private reporting channel.

## Sensitive material

Never upload, attach, paste, or reproduce any of the following in an issue, pull request, discussion, or test fixture:

- SQLite databases, transaction exports, backups, or screenshots.
- Runtime logs that may include transaction descriptions or local paths.
- API keys, tokens, passwords, cookies, private keys, `.env` files, or deployment configuration.

Use synthetic data and redacted excerpts when a reproduction is necessary.
