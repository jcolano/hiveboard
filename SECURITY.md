# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in HiveBoard, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **security@hiveboard.net**

Or use [GitHub Security Advisories](https://github.com/jcolano/hiveboard/security/advisories/new) to report privately.

## What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: within 48 hours
- **Initial assessment**: within 1 week
- **Fix or mitigation**: depends on severity, typically within 2 weeks for critical issues

## Scope

The following are in scope:

- Authentication and authorization bypasses
- API key or JWT token leakage
- Injection vulnerabilities (SQL, command, XSS)
- Data exposure between tenants
- WebSocket security issues

The following are out of scope:

- Issues in dependencies (report upstream)
- Denial of service via expected API usage
- Issues requiring physical access

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |
