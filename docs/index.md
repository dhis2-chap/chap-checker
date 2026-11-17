# chap-checker

[![Rust 2024](https://img.shields.io/badge/rust-2024-orange.svg)](https://github.com/dhis2-chap/chap-checker/blob/main/crates/chap-checker/Cargo.toml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

An uptime monitor for the servers around Chap: DHIS2 instances that integrate
with `chap-core` through a DHIS2 route, chap-core itself, CAPS, Open Geo
Service deployments, and any other URL. Each kind of server is a plugin with
its own checks (see [Checks](guides/checks.md)).

Operating a DHIS2 deployment with `chap-core` means several moving pieces all
have to stay healthy at once:

- the DHIS2 server itself (reachable, accepts credentials, reports a version),
- the `chap` route on DHIS2 that proxies traffic to `chap-core`,
- the `chap-core` service behind that route (alive, reports a version),
- the modeling app on the DHIS2 frontend,
- the climate app on the DHIS2 frontend.

`chap-checker` runs one HTTP probe per piece against each instance it's
pointed at (and the same for chap-core, CAPS or Open Geo Service on their
own), rolls the results up into a non-zero exit code on any failure, and
optionally fires alerts (Slack, generic webhook, or a custom transport) when
something flips between OK and broken — transition-only, so a sustained
outage produces one message on entry and one on recovery, not a ping every
cron tick.

It's designed to be run by cron / a Kubernetes `CronJob` / any scheduler that
cares about exit codes and a quiet stdout when everything is fine. You can
also run it interactively for ad-hoc one-off checks, open the
[terminal dashboard](guides/dashboard.md) for an at-a-glance view, or run
[`chap-checker serve`](guides/serve.md) as a 24/7 daemon backing a browser
dashboard and remote thin-client TUIs.

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

chap-checker is a single binary written in Rust. PyPI carries a wheel per
platform (Linux x86_64 and aarch64, macOS Intel and Apple silicon, Windows),
which is what `uvx` and `uv tool install` pick up; nothing else needs to be
installed.

Released versions are published to
[PyPI](https://pypi.org/project/chap-checker/) and the docs site
tracks the latest tag. See [Releasing](guides/releasing.md) for the
operator-side release playbook.

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
alerts = ["slack", "webhook"]       # fan out to every alerter you opt in

[alerts.slack]
webhook_url_env = "SLACK_WEBHOOK_URL"

[alerts.webhook]
url_env = "OPS_WEBHOOK_URL"          # generic HTTP POST with the canonical envelope
```

Then `chap-checker verify` runs every configured instance and fires the
opted-in alerters on status transitions. `chap-checker init` writes a minimal
annotated template if you'd rather start from a scaffold.

## Where to go next

- **[Configuration](guides/configuration.md)** — the TOML file in detail.
- **[Checks](guides/checks.md)** — what each built-in check does and how they
  depend on each other.
- **[Adding checks and plugins](guides/custom-checks.md)** — how a check and
  a plugin (a kind of server) are added to chap-checker.
- **[Alerting](guides/alerts.md)** — Slack and generic-webhook transports,
  per-instance opt-in, transition semantics, delivery-failure retries.
- **[CLI](guides/cli.md)** — every command and flag.
- **[Cron](guides/cron.md)** — production deployment recipes.
- **[TUI dashboard](guides/dashboard.md)** — the terminal dashboard (with `--connect` for cross-machine consistency, `--kiosk` for a wall).
- **[Server](guides/serve.md)** — long-running daemon, browser dashboard, JSON state API, systemd autostart.

## Links

- [Repository](https://github.com/dhis2-chap/chap-checker)
- [Issues](https://github.com/dhis2-chap/chap-checker/issues)
- [chap-core](https://github.com/dhis2-chap/chap-core) — the service this
  tool monitors behind the DHIS2 route.

## License

AGPL-3.0-or-later
