# chap-checker

[![CI](https://github.com/dhis2-chap/chap-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/dhis2-chap/chap-checker/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/chap-checker)](https://pypi.org/project/chap-checker/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://dhis2-chap.github.io/chap-checker/)

A small command-line health-check and alerting tool for DHIS2 instances that
integrate with `chap-core` via a DHIS2 route. Cron-friendly, with optional
Slack / generic-webhook alerts on status transitions, a long-running
daemon that exposes a browser dashboard (designed for a TV / kiosk) and a
JSON state API, and a Textual TUI for the operator-at-a-desk view (locally
or pointed at a remote daemon).

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

The fastest path is `chap-checker init`, which drops a working `chap-checker.toml` (chmod 600) pointed at the public DHIS2 demo so you can verify the tool runs before adding your own instances:

```bash
chap-checker init
chap-checker verify           # OK on the play demo
```

Then edit `chap-checker.toml`. A typical config:

```toml
[instances.prod]
url = "https://dhis2.example.com"
username = "ops"
password_env = "PROD_PASS"
alerts = ["slack", "webhook"]

[alerts.slack]
webhook_url_env = "SLACK_WEBHOOK_URL"

[alerts.webhook]
url_env = "INCIDENT_BUS_URL"
headers = { "Authorization" = "Bearer ..." }
```

Discover the alert transports and copy-paste their TOML:

```bash
chap-checker alerts list                       # registry of available alerters with per-field comments
chap-checker alerts test --kind both           # fire a synthetic OK->FAIL + FAIL->OK pair
```

Ad-hoc verify against a single instance (no config needed):

```bash
# With a DHIS2 Personal Access Token (recommended on modern servers):
export PROD_TOKEN=...
chap-checker verify --url https://dhis2.example.com --token-env PROD_TOKEN

# Or with a password (Basic auth) read from a named env var:
export PROD_PASSWORD=...
chap-checker verify --url https://dhis2.example.com --username admin --password-env PROD_PASSWORD
```

`--password` / `--token` inline still works but is discouraged — the value lands in shell history and `ps` output. Omit both and you'll be prompted on a TTY. See [chap-checker.toml.example](./chap-checker.toml.example) for the full config template.

### Surfaces

```bash
chap-checker tui          # Textual TUI: operator-at-a-desk view, in a terminal
chap-checker serve        # long-running daemon: browser dashboard at / + JSON state at /api/state
chap-checker tui --connect http://daemon-host:8765   # TUI as a thin client of a remote `serve`
```

Pair them: run `chap-checker serve` somewhere persistent (a small VM, the TV machine itself, systemd-supervised — see [the server guide](https://dhis2-chap.github.io/chap-checker/guides/serve/) for the unit file). Pin a kiosk browser at the URL on the TV; operators at desks run `chap-checker tui` locally or `chap-checker tui --connect http://daemon:8765` for the same numbers without spinning up their own check loop. Alerts fire from one place.

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
- `dhis2_chap_climate_app` — `/api/apps` (matched by `app_hub_id`)

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
