# chap-checker

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

A small command-line health-check and alerting tool for DHIS2 instances that
integrate with `chap-core` via a DHIS2 route. Cron-friendly, with optional
Slack alerts on status transitions and a Textual TUI dashboard for the
at-a-glance "leave it on a TV" view.

**Documentation:** <https://dhis2-chap.github.io/chap-checker>

## Quick start

Ad-hoc against a single instance:

```bash
chap-checker verify \
    --url https://dhis2.example.com \
    --username admin \
    --password REPLACE_ME
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
chap-checker dashboard
```

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
