# CLI reference

This document contains the help content for the `chap-checker` command-line program.

**Command Overview:**

* [`chap-checker`↴](#chap-checker)
* [`chap-checker init`↴](#chap-checker-init)
* [`chap-checker verify`↴](#chap-checker-verify)
* [`chap-checker alerts`↴](#chap-checker-alerts)
* [`chap-checker alerts list`↴](#chap-checker-alerts-list)
* [`chap-checker alerts test`↴](#chap-checker-alerts-test)
* [`chap-checker checks`↴](#chap-checker-checks)
* [`chap-checker checks list`↴](#chap-checker-checks-list)
* [`chap-checker plugins`↴](#chap-checker-plugins)
* [`chap-checker plugins list`↴](#chap-checker-plugins-list)
* [`chap-checker serve`↴](#chap-checker-serve)
* [`chap-checker tui`↴](#chap-checker-tui)

## `chap-checker`

Uptime monitoring for DHIS2 with chap-core, chap-core itself, CAPS, Open Geo Service and any URL, one plugin per kind of server. Cron-friendly, with Slack/webhook alerts on status transitions, a daemon with a browser dashboard, and a terminal dashboard

**Usage:** `chap-checker [OPTIONS] [COMMAND]`

###### **Subcommands:**

* `init` — Create a minimal chap-checker.toml in the current directory
* `verify` — Run every registered check against one or more instances
* `alerts` — Inspect or test configured alerters
* `checks` — Inspect available checks
* `plugins` — Inspect the kinds of server chap-checker can check (`kind = "..."` in an instance)
* `serve` — Launch the chap-checker server
* `tui` — Launch the terminal dashboard (local or `--connect` mode)

###### **Options:**

* `-d`, `--debug` — Enable verbose debug logging on stderr
* `--json` — Emit machine-readable JSON instead of a table (cron-friendly)
* `-q`, `--quiet` — Suppress stdout entirely; just run checks, dispatch alerts, and exit (cron-friendly)
* `-v`, `--version` — Show version and exit



## `chap-checker init`

Create a minimal chap-checker.toml in the current directory.

Drops a single working `instances.play` table pointed at the public DHIS2 demo instance (admin / district) so you can run `chap-checker verify` immediately and replace the values once it works.

**Usage:** `chap-checker init [OPTIONS]`

###### **Options:**

* `-o`, `--output <OUTPUT>` — Where to write the new config

  Default value: `chap-checker.toml`
* `-f`, `--force` — Overwrite the file if it already exists



## `chap-checker verify`

Run every registered check against one or more instances.

With `--url`, runs in ad-hoc mode against that single DHIS2 URL and ignores any config. Auth is Basic (`--username` plus a password from `--password`, `--password-env NAME`, `DHIS2_PASSWORD` or a hidden prompt) or a Personal Access Token (`--token`, `--token-env NAME` or `DHIS2_TOKEN`). Otherwise the config is loaded from `--config` or `./chap-checker.toml`, and every instance runs unless `--instance` picks one.

The exit code is 0 when every check on every target is OK, 1 otherwise. Alerts honor each instance's `alerts = [...]` opt-in; skip them with `--no-alerts`.

**Usage:** `chap-checker verify [OPTIONS]`

###### **Options:**

* `-c`, `--config <CONFIG>` — Path to a TOML config (defaults to ./chap-checker.toml if present)
* `-i`, `--instance <INSTANCE>` — Run only this named instance from the config
* `--url <URL>` — Ad-hoc DHIS2 base URL (bypasses config)
* `-u`, `--username <USERNAME>` — DHIS2 username (ad-hoc mode)
* `-p`, `--password <PASSWORD>` — DHIS2 password (ad-hoc mode). Prefer --password-env or the interactive prompt: inline passwords end up in shell history and `ps` output
* `--password-env <PASSWORD_ENV>` — Name of the env var holding the DHIS2 password (ad-hoc mode). Recommended over --password
* `--token <TOKEN>` — DHIS2 Personal Access Token (ad-hoc mode). Mutually exclusive with --password / --password-env. Prefer --token-env to keep the value out of shell history
* `--token-env <TOKEN_ENV>` — Name of the env var holding the DHIS2 PAT (ad-hoc mode). Recommended over --token
* `--timeout <TIMEOUT>` — HTTP timeout per request (seconds, ad-hoc mode)

  Default value: `10`
* `--insecure` — Skip TLS certificate verification (ad-hoc mode)
* `--check <CHECK>` [alias: `checks`] — Restrict to these check names (transitive `requires` are pulled in). Repeat the flag for multiple. Mirrors the per-instance `checks = [...]` config field
* `--no-alerts` [alias: `no-alert`] — Skip alert dispatch even if configured
* `--state <STATE>` — Path to the persisted state file (default: ./chap-checker.state.json next to the config)
* `--concurrency <CONCURRENCY>` — Number of targets to check in parallel. Overrides the config value if given. Default 5



## `chap-checker alerts`

Inspect or test configured alerters

**Usage:** `chap-checker alerts <COMMAND>`

###### **Subcommands:**

* `list` — List registered alerter types and their TOML fields
* `test` — Send a synthetic transition to every configured alerter (or a named one)



## `chap-checker alerts list`

List registered alerter types and their TOML fields

**Usage:** `chap-checker alerts list`



## `chap-checker alerts test`

Send a synthetic transition to every configured alerter (or a named one).

Useful after rotating a credential or when you suspect the alert pipeline is broken. Each run posts a real message to the configured receiver, so don't put this on a cron. Exit code is 0 only when every alerter delivered.

**Usage:** `chap-checker alerts test [OPTIONS]`

###### **Options:**

* `-n`, `--name <NAME>` — Send only to this alerter (must be a configured alerter name)
* `-k`, `--kind <KIND>` — Which synthetic transition(s) to send

  Default value: `failure`

  Possible values: `failure`, `recovery`, `both`

* `-c`, `--config <CONFIG>` — Path to a TOML config (defaults to ./chap-checker.toml if present)



## `chap-checker checks`

Inspect available checks

**Usage:** `chap-checker checks <COMMAND>`

###### **Subcommands:**

* `list` — List registered checks



## `chap-checker checks list`

List registered checks

**Usage:** `chap-checker checks list`



## `chap-checker plugins`

Inspect the kinds of server chap-checker can check (`kind = "..."` in an instance)

**Usage:** `chap-checker plugins <COMMAND>`

###### **Subcommands:**

* `list` — List every plugin with its checks and a copy-paste instance table



## `chap-checker plugins list`

List every plugin with its checks and a copy-paste instance table

**Usage:** `chap-checker plugins list`



## `chap-checker serve`

Launch the chap-checker server.

A long-running daemon: it runs the check loop, dispatches alerts (with `--alerts`), serves JSON state at `/api/state` and, unless `--no-ui`, a browser dashboard at `/`. The TUI's `--connect` mode and any browser pointed at it see the same snapshot. Meant to run under systemd on a TV machine or a small VM; see the Server guide for the unit file.

**Usage:** `chap-checker serve [OPTIONS]`

###### **Options:**

* `-c`, `--config <CONFIG>` — Path to a TOML config (defaults to ./chap-checker.toml if present)
* `--interval <INTERVAL>` — Server-side check refresh interval (seconds)

  Default value: `30`
* `--alerts` — Dispatch alerts (Slack, webhook, ...) from refresh cycles. Off by default
* `--no-alerts` — Don't dispatch alerts (the default)
* `--state <STATE>` — State file path (default: ./chap-checker.state.json next to the config)
* `--host <HOST>` — Bind address. Use 0.0.0.0 to expose on the local network (e.g. for a TV)

  Default value: `127.0.0.1`
* `--port <PORT>` — Port to listen on

  Default value: `8765`
* `--ui` — Serve the browser dashboard at `/` (the default)
* `--no-ui` — API only: skip the browser dashboard; `/` returns 404, `/api/*` is unchanged



## `chap-checker tui`

Launch the terminal dashboard (local or `--connect` mode).

The rail lists every instance with its worst status; the overview shows four summary tiles and a panel per instance; choosing one opens its page. `r` refreshes now, Ctrl+R reloads the config, Ctrl+K opens the palette, `?` lists the keys, Ctrl+Q quits.

Local mode runs the checks itself; whether alerts fire is decided with `--alerts` (off by default). Connect mode (`--connect URL`) shows a remote `chap-checker serve` daemon's state instead; the config, the state file and alerts stay with the daemon.

**Usage:** `chap-checker tui [OPTIONS]`

###### **Options:**

* `-c`, `--config <CONFIG>` — Path to a TOML config (defaults to ./chap-checker.toml if present)
* `--interval <INTERVAL>` — Refresh interval in seconds

  Default value: `30`
* `--alerts` — Dispatch alerts (Slack, webhook, ...) from refresh cycles. Off by default
* `--no-alerts` — Don't dispatch alerts (the default)
* `--state <STATE>` — State file path (default: ./chap-checker.state.json next to the config)
* `--connect <CONNECT>` — Show a remote `chap-checker serve` daemon instead of running checks locally (e.g. http://tv-host:8765). Mutually exclusive with --config / --state / --alerts
* `--token <TOKEN>` — Bearer token for an authenticated daemon (--connect only). Discouraged inline: it lands in shell history and `ps` output; prefer --token-env
* `--token-env <TOKEN_ENV>` — Name of the env var holding the bearer token for --connect. Recommended over --token
* `--kiosk` — Start full screen, without the rail and the top bar: for a wall display. Escape leaves it



