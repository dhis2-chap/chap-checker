# chap-checker

[![CI](https://github.com/dhis2-chap/chap-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/dhis2-chap/chap-checker/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/chap-checker)](https://pypi.org/project/chap-checker/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://dhis2-chap.github.io/chap-checker/)

A small command-line health-check and alerting tool for DHIS2 instances that
integrate with `chap-core` via a DHIS2 route. Cron-friendly, with optional
Slack alerts on status transitions and a Textual TUI dashboard for the
at-a-glance "leave it on a TV" view.

**Documentation:** <https://dhis2-chap.github.io/chap-checker>

## Install

```bash
# One-shot run without installing (no PATH pollution):
uvx chap-checker --version
uvx chap-checker verify --url https://dhis2.example.com --token-env DHIS2_TOKEN

# Persistent install into uv's isolated tool environment:
uv tool install chap-checker
chap-checker --version

# Upgrade to the latest release later:
uv tool upgrade chap-checker

# Or, if you're embedding into another uv project:
uv add chap-checker
```

## Quick start

Ad-hoc against a single instance (credentials resolved safely):

```bash
# With a DHIS2 Personal Access Token (recommended on modern servers):
export PROD_TOKEN=...
chap-checker verify \
    --url https://dhis2.example.com \
    --token-env PROD_TOKEN

# With a password (Basic auth) read from a named env var:
export PROD_PASSWORD=...
chap-checker verify \
    --url https://dhis2.example.com \
    --username admin \
    --password-env PROD_PASSWORD

# Omit --password / --token entirely and you'll be prompted on the
# terminal (hidden input). DHIS2_TOKEN / DHIS2_PASSWORD env vars
# work as defaults too. Passing --password / --token inline still
# works but is discouraged - the value lands in shell history and
# `ps` output. Token and password flags are mutually exclusive.
```

Multiple instances in `./chap-checker.toml`:

```toml
[instances.prod]
url = "https://dhis2.example.com"
username = "ops"
password_env = "PROD_PASS"
alerts = ["slack"]

[alerts.slack]
webhook_url_env = "SLACK_WEBHOOK_URL"
```

Then `chap-checker verify` runs every configured instance and pages Slack on
status transitions. See [chap-checker.toml.example](./chap-checker.toml.example)
for the full template.

The TUI:

```bash
chap-checker tui
```

Or run the server (browser dashboard + JSON state API for remote `tui --connect` clients):

```bash
chap-checker serve
```

Run `chap-checker serve` somewhere persistent (a TV machine, a small VM,
systemd-supervised) and point laptops at it with
`chap-checker tui --connect http://host:8765` so every viewer sees the
same numbers. Pass `--no-ui` for a headless API-only daemon.

## Built-in checks

Two namespaces — `dhis2_*` probes DHIS2 itself, `dhis2_chap_*` probes
chap-core through the DHIS2 route. Each tile in the dashboard, each row in
`chap-checker checks list`, each entry in the JSON output:

- `dhis2_ping` — `/api/me`
- `dhis2_system_info` — `/api/system/info`
- `dhis2_chap_route` — `/api/routes?filter=code:eq:chap`
- `dhis2_chap_ping` — `/api/routes/chap/run/health`
- `dhis2_chap_system_info` — `/api/routes/chap/run/system/info`
- `dhis2_chap_modeling_app` — `/api/apps` (matched by `app_hub_id`)

Full reference + endpoint details: [Built-in checks](https://dhis2-chap.github.io/chap-checker/guides/checks/).

## Development

```bash
make install
make lint        # ruff + mypy + pyright
make test
make docs        # serve docs locally
```

See [Development](https://dhis2-chap.github.io/chap-checker/guides/development/)
for repo layout and house rules.

## License

AGPL-3.0-or-later
