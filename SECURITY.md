# Security Policy

## Supported Versions

Security fixes are provided for the latest code on the default branch and the
latest published release. Older releases may not receive backported fixes.

## Reporting a Vulnerability

If you believe you found a security issue, please report it privately before
opening a public issue.

Preferred reporting options:

- Use GitHub private vulnerability reporting if it is enabled for this
  repository.
- Otherwise, contact the repository owner through GitHub and include
  "Video Automation security report" in the message title.

Please include:

- The affected version, release, or commit.
- Clear steps to reproduce the issue.
- The expected impact.
- Whether the local API was exposed beyond `127.0.0.1`.
- Relevant logs or screenshots with secrets removed.

Do not include API keys, cookies, platform credentials, private videos, job
outputs, or `.env` files in a public issue.

## Security Expectations

Video Automation is a local-first tool. By default, the Web API binds to
`127.0.0.1`, which is intended for local use on your own machine.

If you change the API host to `0.0.0.0`, expose it on a LAN, or put it behind a
public domain, you are responsible for adding appropriate protection such as:

- Firewall rules.
- Reverse-proxy authentication.
- Network access controls.
- HTTPS termination when exposed outside the local machine.

## Sensitive Data

Keep the following files and data private:

- `.env` and any local configuration files containing keys.
- OpenAI, Google, OpenRouter, platform, or model-provider credentials.
- Cookies, OAuth tokens, and browser session data.
- Downloaded media, private recordings, generated job outputs, and publish
  packages.

The repository `.gitignore` excludes common local runtime directories such as
`input/`, `processing/`, `logs/`, `dist/`, and `.env`.

## Updates

Video Automation has no remote self-update mechanism. Obtain updates from the
GitHub Releases page or build them from source, and verify that you are using
the intended repository and release before installing.
