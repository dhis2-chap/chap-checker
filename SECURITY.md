# Security policy

## Reporting a vulnerability

**Please do not open a public GitHub issue** for security problems in chap-checker. Two private channels:

1. **Preferred — GitHub private vulnerability reporting.** Open <https://github.com/dhis2-chap/chap-checker/security/advisories/new> and fill in the form. This gives us a structured intake, a private timeline, and a clean path to a CVE / advisory once the fix is out.
2. **Fallback — email.** If you can't use GitHub's flow, mail [morten@dhis2.org](mailto:morten@dhis2.org). Subject line: `[chap-checker security]`.

Include:

- a description of the issue and its impact,
- the chap-checker version (`chap-checker --version`),
- the platform (OS, Python version) and the DHIS2 server version (`dhis2_system_info` output) if relevant,
- repro steps or a proof-of-concept,
- whether you'd like to be credited in the advisory.

## What you can expect

- **Acknowledgement** within 3 working days.
- **Triage and severity classification** within 7 working days.
- **Fix timeline** depends on severity; we aim for a patched PyPI release within 14 working days for High / Critical issues, longer for low-severity issues batched into a regular release.
- **Coordinated disclosure**: we'd like a chance to ship the fix before the issue becomes public; please give us a reasonable embargo window. We'll credit you in the advisory unless you'd rather stay anonymous.

## Scope

chap-checker is a CLI / daemon for monitoring DHIS2 instances. In scope:

- Any vulnerability in the chap-checker code (the Python package, the bundled web UI, the daemon's HTTP surface).
- Credential or secret leakage in logs, error messages, or persisted state files.
- Authentication / authorization issues in the `chap-checker serve` HTTP surface. As of 0.7, the daemon supports optional bearer-token auth via the `[auth]` block (see `docs/guides/serve.md#authentication`); auth is **off by default for backwards compatibility**, so a report of "no auth on /api/state on a deployment without `[auth]` configured" is not a vulnerability. Any way to bypass the token when `[auth]` *is* set, or to read the token from a non-protected response, is in scope.

Out of scope:

- Vulnerabilities in DHIS2 itself, in `dhis2w-client`, or in upstream dependencies — please report those to the relevant project.
- Issues that require the operator to install a malicious config file or run arbitrary code as the chap-checker process.

## Supported versions

We patch the latest `0.x` release. Pre-1.0 alpha releases below the current one don't get backports — please upgrade to the latest version before reporting against an older one.

| Version | Supported |
|---------|-----------|
| 0.8.x   | Yes       |
| < 0.8.0 | No        |
