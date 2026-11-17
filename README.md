# chap-checker

[![CI](https://github.com/dhis2-chap/chap-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/dhis2-chap/chap-checker/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/chap-checker)](https://pypi.org/project/chap-checker/)
[![Rust 2024](https://img.shields.io/badge/rust-2024-orange.svg)](crates/chap-checker/Cargo.toml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://dhis2-chap.github.io/chap-checker/)

An uptime monitor for the servers around Chap: DHIS2 instances that
integrate with `chap-core` through a DHIS2 route, chap-core itself, CAPS,
[Open Geo Service](https://github.com/dhis2/open-geo-service) deployments,
and any other URL. Each kind of server is a plugin with its own checks.
Cron-friendly, with optional Slack / generic-webhook alerts on status
transitions, a long-running daemon that serves a browser dashboard (designed
for a TV / kiosk) and a JSON state API, and a terminal dashboard for the
operator at a desk (locally or pointed at a remote daemon).

A single binary, written in Rust and published to PyPI as a wheel per
platform: `uvx chap-checker` runs it with nothing else installed.

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
chap-checker tui          # terminal dashboard: operator-at-a-desk view
chap-checker tui --kiosk  # the same, full screen, for a wall display
chap-checker serve        # long-running daemon: browser dashboard at / + JSON state at /api/state
chap-checker tui --connect http://daemon-host:8765   # TUI as a thin client of a remote `serve`
```

Pair them: run `chap-checker serve` somewhere persistent (a small VM, the TV machine itself, systemd-supervised — see [the server guide](https://dhis2-chap.github.io/chap-checker/guides/serve/) for the unit file). Pin a kiosk browser at the URL on the TV; operators at desks run `chap-checker tui` locally or `chap-checker tui --connect http://daemon:8765` for the same numbers without spinning up their own check loop. Alerts fire from one place.

## Plugins and built-in checks

Each instance names its `kind`, and each kind is a plugin with its own checks
(`chap-checker plugins list` shows them with a copy-paste TOML table):

| `kind` | What it checks |
|---|---|
| `dhis2` (default) | DHIS2, and chap-core through the DHIS2 `chap` route |
| `ogs` | An Open Geo Service (openEO backend) deployment |
| `chap` | chap-core reached directly: `/health`, `/system/info` |
| `caps` | CAPS: its version, and whether it reaches chap-core and DHIS2 |
| `http` | Any URL: an expected status (default 2xx), and optionally body text or a JSON value |

An instance only runs the checks for its `kind`. Each row in the dashboards,
each row in `chap-checker checks list`, each entry in the JSON output:

- `http_2xx` — unauthenticated `GET <base_url>`, asserts the final response is 2xx (follows redirects); `dhis2` and `ogs`
- `dhis2_ping` — `/api/me`
- `dhis2_system_info` — `/api/system/info`
- `dhis2_chap_route` — `/api/routes?filter=code:eq:chap`
- `dhis2_chap_ping` — `/api/routes/chap/run/health`
- `dhis2_chap_system_info` — `/api/routes/chap/run/system/info`
- `dhis2_chap_modeling_app` — `/api/apps` (matched by `app_hub_id`)
- `dhis2_chap_climate_app` — `/api/apps` (matched by `app_hub_id`)
- `ogs_health` — `/health` (liveness only)
- `ogs_info` — `/info` + `/` (version and openEO capabilities)
- `chap_health` — `/health` on chap-core, `status` must be `success`
- `chap_system_info` — `/system/info` on chap-core (version, revision)
- `caps_system_info` — `/system/info` on CAPS (version)
- `caps_chap`, `caps_dhis2` — CAPS reports chap-core / DHIS2 connected
- `http_expect` — any URL, with `expect_status`, `body_contains`, `json_path` / `json_equals`

Full reference + endpoint details: [Checks](https://dhis2-chap.github.io/chap-checker/guides/checks/).

## Development

```bash
make install     # dev tools (uv) and a debug build (cargo)
make lint        # cargo fmt + clippy
make test        # cargo test
make build       # the wheel + sdist into dist/, as the release does
make docs        # serve docs locally
```

See [Development](https://dhis2-chap.github.io/chap-checker/guides/development/)
for repo layout and house rules.

## Contributing

PRs welcome — start with [CONTRIBUTING.md](./CONTRIBUTING.md). The development guide on the docs site has the long version: <https://dhis2-chap.github.io/chap-checker/guides/development/>.

For security issues, see [SECURITY.md](./SECURITY.md) — please don't open a public issue.

Contributors: [github.com/dhis2-chap/chap-checker/graphs/contributors](https://github.com/dhis2-chap/chap-checker/graphs/contributors).

## License

[AGPL-3.0-or-later](./LICENSE).
