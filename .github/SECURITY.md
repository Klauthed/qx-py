# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x (main) | ✅ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report them privately using one of these methods:

- **GitHub private advisory**: [Report a vulnerability](../../security/advisories/new) (preferred)
- **Email**: security@klauthed.com

Include as much of the following as possible:

- Description of the vulnerability and its impact
- Steps to reproduce (proof-of-concept or minimal repro)
- Which package(s) and version(s) are affected
- Any suggested fix or mitigation

We aim to:

- Acknowledge the report within **2 business days**
- Provide an initial assessment within **5 business days**
- Publish a fix and advisory within **30 days** for confirmed vulnerabilities (shorter for critical issues)

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure) — please give us reasonable time to patch before any public disclosure.

## Scope

This policy covers all packages in this repository (`qx-core`, `qx-di`, `qx-cqrs`, `qx-db`, `qx-http`, `qx-auth`, and the rest). It does not cover third-party dependencies — please report those to their respective maintainers.
