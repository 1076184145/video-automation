# Security Policy

## Supported Versions

The main branch is the only supported development line unless a release branch
is explicitly documented.

## Reporting a Vulnerability

Please do not publish a working exploit before maintainers have had a chance to
review and fix the issue.

For now, report security issues by opening a private communication channel with
the project maintainer, or by using GitHub's private vulnerability reporting if
it is enabled for the repository.

Include as much detail as possible:

- Affected version or commit.
- Steps to reproduce.
- Expected and actual impact.
- Whether the issue requires exposing the local API beyond `127.0.0.1`.
- Relevant logs with secrets redacted.

## Local-First Security Notes

Video Automation is designed as a local-first tool. The default API host is
`127.0.0.1`. If you bind the API to `0.0.0.0` or expose it on a LAN or the
internet, add your own authentication, firewall, or reverse-proxy protection.

Never commit `.env`, API keys, platform credentials, model tokens, cookies,
downloaded media, generated job outputs, or publish packages.
