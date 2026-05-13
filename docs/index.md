# chap-checker

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

A small command-line health-check and alerting tool for DHIS2 instances that
integrate with `chap-core` via a DHIS2 route.

Operating a DHIS2 deployment with `chap-core` means several moving pieces all
have to stay healthy at once:

- the DHIS2 server itself (reachable, accepts credentials, reports a version),
- the `chap` route on DHIS2 that proxies traffic to `chap-core`,
- the `chap-core` service behind that route (alive, reports a version),
- the modeling app on the DHIS2 frontend.

`chap-checker` runs one HTTP probe per piece against each DHIS2 instance it's
pointed at, rolls the results up into a non-zero exit code on any failure, and
optionally posts a Slack message when something flips between OK and broken
(transition-only — no Slack spam during a sustained outage, one recovery alert
when it comes back).

It's designed to be run by cron / a Kubernetes `CronJob` / any scheduler that
cares about exit codes and a quiet stdout when everything is fine. You can
also run it interactively for ad-hoc one-off checks, or open the
[Textual dashboard](guides/dashboard.md) for an at-a-glance TV view.

## Install

```bash
uv add chap-checker
# or
pipx install chap-checker
```

## Quick start

Ad-hoc against a single instance (password resolved safely):

```bash
# Recommended: read the password from a named environment variable.
export PROD_PASSWORD=...
chap-checker verify \
    --url https://dhis2.example.com \
    --username admin \
    --password-env PROD_PASSWORD

# Alternative: omit --password / --password-env entirely and you'll be
# prompted on the terminal (hidden input). The DHIS2_PASSWORD env var
# also works as a default.

# Passing --password inline still works but is discouraged - the value
# ends up in shell history and `ps` output.
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
status transitions.

## Where to go next

- **[Configuration](guides/configuration.md)** — the TOML file in detail.
- **[Built-in checks](guides/checks.md)** — what each check does and how they
  depend on each other.
- **[Alerting (Slack)](guides/alerts.md)** — setting up an Incoming Webhook,
  per-instance opt-in, transition semantics.
- **[CLI reference](guides/cli.md)** — every command and flag.
- **[Cron deployment](guides/cron.md)** — production deployment recipes.
- **[TUI dashboard](guides/dashboard.md)** — the at-a-glance Textual UI.
- **[Adding a custom check](guides/custom-checks.md)** — the `@register_check`
  decorator and Protocol contract.

## Links

- [Repository](https://github.com/dhis2-chap/chap-checker)
- [Issues](https://github.com/dhis2-chap/chap-checker/issues)
- [chap-core](https://github.com/dhis2-chap/chap-core) — the service this
  tool monitors behind the DHIS2 route.

## License

AGPL-3.0-or-later
