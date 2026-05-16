# Changelog

All notable changes to **chap-checker** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Versions before 1.0 are alpha — breaking changes can land in any minor release; patch releases are bug fixes and docs only.

## [Unreleased]

## [0.8.1] — 2026-05-16

### Fixed

- **Alert dispatch is now serialised via a file lock around load -> compute -> dispatch -> save.** Previously `dispatch_alerts_async()` did a plain `load_state -> compute_transitions -> alerter.notify -> save_state` cycle. Only the final `save_state` was atomic (via `os.replace`); the load-through-save window itself was unguarded. Two concurrent runs - overlapping cron + a manual `chap-checker verify`, or `tui` and `serve` sharing one state file - could each read the same prior state and re-emit the same transition, duplicating alerts to Slack / webhook receivers. New `alert_state_lock(state_path, timeout_s=30.0)` async context manager in `state_store.py` uses `fcntl.flock` on a sidecar `<state>.lock` file. POSIX-only; Windows operators should keep alert dispatch single-process (the lock is a no-op there). Lock contention beyond the timeout raises `StateLockTimeout`; the dispatch function logs + skips this tick instead of crashing the background refresh loop. Two new regression tests in `tests/test_state_store.py` (serialisation + timeout).

### Changed

- **`dhis2_chap_ping` and `dhis2_chap_system_info` now produce the same structured failure shape as the other DHIS2 checks.** Both used to return a generic `"Unexpected status 401 from /api/routes/..."` line with no `details`; they now go through `diagnose_status()` like the rest, so 401 / 403 / 404 messages explain the credential / authority / endpoint cause and every failure carries `http_status` + `path` in `details`. Easier to route on for alert receivers and the JSON-API tooling. The 502 special case (chap-core didn't respond) keeps its message but also gains structured `http_status: 502`. Four new regression tests in `tests/test_check_response_guards.py`.

## [0.8.0] — 2026-05-16

### Fixed

- **Permission advisory now catches every inline secret in the TOML.** Before, the `mode 0644` warning only fired for instance `password` / `token` and `[alerts.slack].webhook_url`. It now also covers `[alerts.webhook].url`, any `[alerts.webhook].headers` (typical home for `Authorization: Bearer ...` / API keys), and `[auth].token`. Env-var indirections (`*_env`) are still skipped — they don't make the file itself sensitive. New parametrised regression test in `tests/test_config.py`.
- **`dhis2_chap_modeling_app` / `dhis2_chap_climate_app` now FAIL cleanly on malformed `/api/apps` entries.** Previously, a dict entry whose values pydantic couldn't coerce raised an uncaught `ValidationError` out of the check, surfacing as a generic `ERROR / Crashed` tile in the runner. Both checks now wrap `Dhis2App.model_validate(...)` and convert the failure to a check-specific `FAIL` with the pydantic message attached. Two new regression tests.

### Changed

- **Docs**: `docs/guides/checks.md` no longer claims the modeling- and climate-app checks depend on `dhis2_ping` — they require `dhis2_chap_route` (which transitively pulls `dhis2_ping`). Matches the code at `src/chap_checker/checks/dhis2_chap_modeling_app.py:50` and `src/chap_checker/checks/dhis2_chap_climate_app.py:34`.
- **Docs**: `docs/guides/configuration.md` no longer says `[alerts.*]` is "only slack today" — `webhook` has shipped since 0.5.
- **Docs**: `docs/guides/alerts.md`'s "alerts test" section now mentions the `--kind` flag and the actual default (`failure`, an OK→FAIL transition; the previous text said FAIL→OK which is the `recovery` kind).
- **Docs**: regenerated `docs/cli-reference.md` after rephrasing two CLI docstrings whose `[instances.NAME]` text was being eaten by the typer doc generator (the rendered file had `Drops a single working \`\` block ...` and `Every \`\` block runs ...`).

## [0.7.4] — 2026-05-16

### Fixed

- **`POST /api/reload` now applies a new `[auth]` block.** Previously the FastAPI auth dependency closed over the bearer token at app-build time, so adding / removing / changing the `[auth]` block in the TOML and reloading left the prior protection in place until the daemon restarted. The dependency now reads `server.resolved_auth_token` at request time, and `reload()` re-resolves the token from the new config (failing the reload with 400 if `auth.token_env` references a missing env var, which leaves the prior auth state intact). Three regression tests cover add / remove / rotate.

### Added

- **TUI guide now shows the dhis2-themed dashboard and both auth-modal variants.** The `dashboard-dhis2.svg` artifact existed but was never referenced from the prose; the new `tui-token-modal-{phosphor,dhis2}.svg` pair gives the modal the same theme parity the browser screenshots already had. Naming follows the existing `dashboard.svg` / `dashboard-dhis2.svg` convention (default name = phosphor, `-dhis2` suffix = DHIS2 theme).

## [0.7.3] — 2026-05-16

### Fixed

- **TUI theme race on `tui --connect URL`.** `TokenPromptScreen` was painting in the default phosphor theme before the daemon's `[ui].theme` was known, briefly flashing green-on-black before snapping to the right palette once `/api/state` returned. `DashboardApp.on_mount` now `awaits` a one-shot `_probe_remote_theme()` call against `/api/auth` and sets `self.theme` *before* anything paints. Same fix shape as the browser modal's pre-paint apply, just on the TUI side.

### Added

- **`scripts/capture_token_modal.py`** — committed Textual capture script that drives `DashboardApp(connect_url=URL)` through to the auth-token modal and saves an SVG via `app.save_screenshot()`. Used to verify the theme-race fix and any future modal change.
- **`scripts/capture_browser.py`** — committed Playwright async-API capture script that drives the browser dashboard end-to-end (login modal → signed-in dashboard → sign-out) and writes three PNGs. Replaces ad-hoc Playwright MCP runs for repeatable visual checks.
- **`playwright`** added to the `dev` dependency group (chromium binary installed via `uv run playwright install chromium`).
- **CLAUDE.md rule 6**: any UI-affecting change (TUI or browser) must be visually verified against a rendered artifact before review — `scripts/` covers the common flows.

## [0.7.2] — 2026-05-16

### Changed

- **Token prompt is now an in-TUI Textual modal** instead of a typer one-liner at the CLI before the dashboard starts. `chap-checker tui --connect URL` (no token) launches the TUI, hits its first 401, and pops a centred `TokenPromptScreen` with an `Input` for the token. Submit (or Enter) rebuilds the httpx client with the bearer header and refreshes; cancel (Escape) paints the auth-rejected banner. UX matches the browser modal — same surface, same flow.

### Added

- `TokenPromptScreen` — reusable `ModalScreen[str | None]` in `chap_checker.dashboard` for custom checks / future flows that need a quick masked-input prompt.
- Three Textual-pilot tests covering: 401 with no token shows the modal; submitting sets the bearer header + re-fetches; Escape paints the banner and doesn't re-prompt on subsequent ticks.

### Removed

- CLI-level `typer.prompt("Token", hide_input=True)` fallback for `tui --connect`. The TUI modal supersedes it; non-TTY invocations still fall through cleanly to the auth-rejected banner.

## [0.7.1] — 2026-05-16

### Fixed

- **Browser theme race after sign-in.** The login modal's pre-paint apply wrote CSS variables to `:root` and raced with the artifact's `applyTheme()`, leaving `--header-bg` unset on first sign-in (the DHIS2 blue header bar was missing until a manual reload). The modal now renders with literal inline colours pulled from its own self-contained palette — no `:root` writes — and the artifact owns theme variables end-to-end.
- **Immediate refetch on sign-in.** The auth-bus wrapper hack in `_auth.js` recorded subscribers in a separate dict that `_state.js`'s polling `pull` never reached, so the dashboard waited a full poll tick (~5s) after sign-in before appearing. `_state.js` now exposes `emit` on the public bus; `_auth.js` calls it directly. Dashboard renders in the next render cycle.

### Added

- **Sign-out button in the top-right header** of the browser dashboard (visible when a token is stored). Replaces the previous "reload config" pill — reload still available via Ctrl+K → "Reload config".
- **DHIS2 blue accent on the dhis2 theme's login modal** — title heading + Sign in button now use `#1f4d75`, matching the signed-in dashboard's header strip.
- **Theme-aware modal palette**: each theme (phosphor / amber / high / tokyo / dhis2) has its own `titleInk` / `inputBorder` / `btnBg` / `btnInk` / `btnBorder` so the modal feels native everywhere.
- **`tui --connect URL` interactive token prompt**: if neither `--token` nor `--token-env` is given on a TTY, the TUI probes `/api/auth` and prompts for the token interactively (hidden input). Same UX as `verify --url --token`. Non-TTY runs silently fall through to the "auth rejected" banner.
- **Heads-up for `tui` local mode** when `[auth]` is configured: prints a one-line note explaining that `[auth]` only gates `chap-checker serve` (the HTTP daemon), not local TUI mode which runs in the terminal and isn't network-reachable.
- **Login modal screenshot** in the Server guide (`web-dashboard-login-dhis2.png`).

### Changed

- **All browser screenshots regenerated** to include the new sign-out button + auth-enabled deployment.
- **Docs**: `chap-checker.toml.example` + `DEFAULT_INIT_TEMPLATE` clarify that `[auth]` is `serve`-only.

## [0.7.0] — 2026-05-16

### Added

- **Bearer-token auth on `chap-checker serve`**. Optional `[auth]` block in the TOML (`token` / `token_env`) protects `/api/state` and `/api/reload`. Server-side comparison is constant-time (`hmac.compare_digest`). Off by default for backwards compatibility — without the block, the daemon behaves like 0.6.x.
- **Login modal in the browser dashboard**. When auth is on, the SPA gets a 401 on first load, renders a token-entry modal, stores the value in `localStorage`, and retries. A new **Sign out** entry in the Ctrl+K / ⌘K command palette clears the stored token. Lives in `_auth.js` alongside `_state.js`, outside the designer-replaceable `src/` tree.
- **`chap-checker tui --connect URL --token-env NAME` / `--token VALUE`**. Sends the bearer header on every fetch. A 401 paints a distinct `"auth rejected by ..."` banner instead of a generic disconnect message.
- **`GET /api/auth`** — unprotected probe returning `{"required": true|false}` so clients (the SPA, scrapers) can detect whether to attach a header without parsing 401 responses.
- **Startup warning** when `serve --host 0.0.0.0` is launched without `[auth]` configured. Non-fatal — operators on hardened LANs or behind reverse proxies can ignore it.

### Changed

- README: dropped the **PyPI downloads** badge (upstream rate-limits it on shields.io) and the **Conventional Commits** badge (a contributor convention; lives in CONTRIBUTING.md). Five badges down from seven.
- `config.py`: extracted a single `_resolve_value_or_env()` helper for the "literal or env-var" pattern; new alerters / auth blocks use it instead of reimplementing the dance.

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

[Unreleased]: https://github.com/dhis2-chap/chap-checker/compare/v0.8.1...HEAD
[0.8.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.8.1
[0.8.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.8.0
[0.7.4]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.7.4
[0.7.3]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.7.3
[0.7.2]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.7.2
[0.7.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.7.1
[0.7.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.7.0
[0.6.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.6.0
[0.5.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.5.1
[0.5.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.5.0
[0.4.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.4.1
[0.4.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.4.0
[0.3.1]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.3.1
[0.3.0]: https://github.com/dhis2-chap/chap-checker/releases/tag/v0.3.0
