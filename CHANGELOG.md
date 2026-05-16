# Changelog

All notable changes to **chap-checker** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Versions before 1.0 are alpha — breaking changes can land in any minor release; patch releases are bug fixes and docs only.

## [Unreleased]

## [0.6.0] — 2026-05-16

### Added

- **Generic `webhook` alerter**. POSTs a canonical JSON envelope (`checker_version`, `summary`, `transitions[]`) to any URL that accepts `application/json`. Configure with `[alerts.webhook]` (`url` / `url_env`, `headers`, `notify_on`, `timeout_s`). Use it for PagerDuty Events, OpsGenie, an internal incident bus, n8n / Make automations, or any custom receiver.
- **`chap-checker alerts test --kind {failure|recovery|both}`**. Lets operators exercise both transition directions (or both at once) when verifying an alert pipeline.
- **Rich result table** in `alerts test` output. One row per alerter with a coloured status cell.
- **`chap-checker alerts list`** now registry-driven (no `--config` required). Renders one Rich panel per alerter with a copy-paste-ready TOML snippet and per-field comments. Each alerter ships its own `toml_example` ClassVar.

### Changed

- **`SlackAlerter` now subclasses `WebhookAlerter`**. The HTTP transport, timeout, and error contract live in one place; Slack only owns the Block Kit payload shape via `_build_payload`. Wire-level output is identical to 0.5.x.
- **`alerts/__init__.py` auto-imports `slack` and `webhook` modules** so every `@register_alerter` decorator fires at import. Mirrors the pattern in `checks/__init__.py`.
- **`chap-checker alerts test` docstring** generalised — no longer Slack-specific.
- **README quick-start** rewritten for the current shape (init flow, both alert transports, TUI-vs-server roles clarified).

### Removed

- **`RELEASE.md`** at repo root. It was a redirect stub; the operator playbook lives at `docs/guides/releasing.md`.

## [0.5.1] — 2026-05-16

### Added

- **Endpoint-specific permission diagnostics** for `/api/system/info`, `/api/routes`, and `/api/apps`. 401 / 403 / 404 now produce distinct, actionable messages (credentials / missing authority / endpoint absent) with structured `details` carrying `http_status`, `path`, and an optional `required_authority` (e.g. `M_dhis-web-app-management`, `F_ROUTE_READ_PRIVATE`).
- New `diagnose_status()` helper in `chap_checker.checks.base` for custom checks.

## [0.5.0] — 2026-05-16

### Added

- **`CheckContext`** threaded through every `Check.run(client, ctx)`. Carries `dhis2_version: Dhis2 | None` (populated by `dhis2_system_info`) and `prior_results` so later checks can pick a v41/v42/v43-typed payload parser while still using `client.get_response()` for status-aware probes.
- **`[retry]` policy block** mapping to `dhis2w_client.RetryPolicy`. Top-level default, optional per-instance override. Off by default — health checkers usually want to observe flakes.
- **`Dhis2Target.open(version=...)`** to open a version-pinned typed client when a check needs typed accessors.

### Changed (**breaking**)

- **`Check.run` signature** gains a `ctx: CheckContext` parameter. Any custom check needs a one-line update.

## [0.4.1] — 2026-05-16

### Fixed

- Empty `Request failed: ` error messages on `httpx.TimeoutException`. New `format_request_error()` helper always carries the exception type and falls back to `"(no message)"`.
- Chap-app checks (`dhis2_chap_modeling_app`, `dhis2_chap_climate_app`) gate on `dhis2_chap_route` rather than `dhis2_ping`. Plain DHIS2 instances now SKIP the chap probes cleanly instead of FAIL-ing on every poll.
- `docs/guides/custom-checks.md` example: switched to `client.get_response(path)`; `client.get(path, model=...)` is the typed alternative.
- README missing `dhis2_chap_climate_app`; development docs referenced removed `web.py`.

## [0.4.0] — 2026-05-15

### Added

- **`chap-checker tui --connect URL`**. Makes the TUI a thin client of a remote `chap-checker serve`. Polls `/api/state` each tick; disconnect surfaces as a red banner. Solves cross-machine drift between a TV and a laptop.
- **Shared `DashboardServer` daemon** in `chap_checker.daemon`. Both surfaces (TUI, browser) consume it. `DashboardServer` / `TileTracker` converted from `@dataclass` to `pydantic.BaseModel` to match the project rule.
- **`AccessLogMiddleware`** for `serve`: one `chap_checker.serve.access` log line per HTTP request (`client "METHOD path HTTP/1.1" status bytes Xms ua="..."`). Per-refresh info line from the daemon. Uvicorn's chatter routes through the same handler.
- **`--no-ui`** flag on `serve` for headless API-only deployments.
- **systemd unit + launchd plist** in `docs/guides/serve.md`.

### Changed (**breaking**)

- **`chap-checker dashboard` → `chap-checker tui`**. `dashboard` was ambiguous since both surfaces are dashboards.
- **`chap-checker web` → `chap-checker serve`**. The command starts a long-running daemon; `serve` matches the role.

## [0.3.1] — 2026-05-15

### Fixed

- The init template now scopes `[instances.play]` to `checks = ["dhis2_ping", "dhis2_system_info"]`. The DHIS2 play instance doesn't ship the chap-core integration, so the previous "run every check" default reported the demo as FAIL.
- `chap-checker init` writes the file `0600` on POSIX (template carries an inline password).
- Generated template expanded with commented reference blocks (env-var auth, token auth, `[alerts.slack]`).

## [0.3.0] — 2026-05-15

### Added

- **`chap-checker init`**. Drops a minimal `chap-checker.toml` in the working directory pointed at the public DHIS2 demo so a fresh install can `chap-checker verify` immediately.

## Earlier

For 0.1.x / 0.2.x release notes, see the [GitHub Releases page](https://github.com/dhis2-chap/chap-checker/releases).

[Unreleased]: https://github.com/dhis2-chap/chap-checker/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.6.0
[0.5.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.5.1
[0.5.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.5.0
[0.4.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.4.1
[0.4.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.4.0
[0.3.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.3.1
[0.3.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.3.0
