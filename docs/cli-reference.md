# CLI reference

Health-check CLI for DHIS2 instances integrated with chap-core. Cron-friendly with Slack/webhook alerts on status transitions and a TUI dashboard for at-a-glance monitoring.

**Usage**:

```console
$ chap-checker [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-d, --debug`: Enable verbose debug logging on stderr.
* `--json`: Emit machine-readable JSON instead of a Rich table (cron-friendly).
* `-q, --quiet`: Suppress stdout entirely; just run checks, dispatch alerts, and exit (cron-friendly).
* `-v, --version`: Show version and exit.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `init`: Create a minimal chap-checker.toml in the...
* `verify`: Run every registered check against one or...
* `tui`: Launch the Textual TUI dashboard (local or...
* `serve`: Launch the chap-checker server.
* `alerts`: Inspect or test configured alerters.
* `checks`: Inspect available checks.

## `chap-checker init`

Create a minimal chap-checker.toml in the current directory.

Drops a single working `instances.play` TOML table pointed at the
public DHIS2 demo instance (admin / district) so you can run
`chap-checker verify` immediately and replace the values once it works.

**Usage**:

```console
$ chap-checker init [OPTIONS]
```

**Options**:

* `-o, --output PATH`: Where to write the new config (default: ./chap-checker.toml).  [default: chap-checker.toml]
* `-f, --force`: Overwrite the file if it already exists.
* `--help`: Show this message and exit.

## `chap-checker verify`

Run every registered check against one or more DHIS2 instances.

Source of targets is decided as follows:

1. If `--url` is given, chap-checker runs in *ad-hoc* mode against
   that single URL and ignores any TOML config. Auth is one of:

   - **Password (Basic)** - requires `--username` plus a password.
     Password resolves from `--password`, `--password-env NAME`,
     the `DHIS2_PASSWORD` env var, or a hidden TTY prompt.
   - **Token (DHIS2 PAT)** - `--token`, `--token-env NAME`, or
     `DHIS2_TOKEN` env. Sent as `Authorization: ApiToken &lt;value&gt;`.
     `--username` is optional in token mode.

   Token and password flags are mutually exclusive; passing both
   errors out.
2. Otherwise the TOML file is loaded from `--config` if given, or
   from `./chap-checker.toml` if present. Every `instances.NAME`
   TOML table runs unless `--instance` narrows the run to one.

Exit code is 0 when every check on every target is `OK`, non-zero
otherwise. Alert dispatch honors each instance&#x27;s `alerts = [...]`
opt-in across every configured alerter (Slack, webhook, custom);
skip dispatch entirely with `--no-alerts`.

**Usage**:

```console
$ chap-checker verify [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `-i, --instance TEXT`: Run only this named instance from the config.
* `--url TEXT`: Ad-hoc DHIS2 base URL (bypasses config).  [env var: DHIS2_URL]
* `-u, --username TEXT`: DHIS2 username (ad-hoc mode).  [env var: DHIS2_USERNAME]
* `-p, --password TEXT`: DHIS2 password (ad-hoc mode). Prefer --password-env or the interactive prompt - inline passwords end up in shell history and `ps` output.  [env var: DHIS2_PASSWORD]
* `--password-env TEXT`: Name of the env var holding the DHIS2 password (ad-hoc mode). Recommended over --password.
* `--token TEXT`: DHIS2 Personal Access Token (ad-hoc mode). Mutually exclusive with --password / --password-env. Prefer --token-env to keep the value out of shell history.  [env var: DHIS2_TOKEN]
* `--token-env TEXT`: Name of the env var holding the DHIS2 PAT (ad-hoc mode). Recommended over --token.
* `--timeout FLOAT`: HTTP timeout per request (seconds, ad-hoc mode).  [default: 10.0]
* `--insecure`: Skip TLS certificate verification (ad-hoc mode).
* `--check, --checks TEXT`: Restrict to these check names (transitive `requires` are pulled in). Repeat the flag for multiple. Mirrors the per-instance `checks = [...]` config field.
* `--no-alerts, --no-alert`: Skip alert dispatch even if configured.
* `--state PATH`: Path to the persisted state file (default: ./chap-checker.state.json next to the config).  [env var: CHAP_CHECKER_STATE]
* `--concurrency INTEGER RANGE`: Number of targets to check in parallel. Overrides the config value if given. Default 5.  [1&lt;=x&lt;=100]
* `--help`: Show this message and exit.

## `chap-checker tui`

Launch the Textual TUI dashboard (local or `--connect` mode).

One tile per configured instance, in an adaptive grid (1-4 columns
depending on instance count). Each tile shows the rolled-up status,
the cumulative ping success ratio, and a per-check breakdown. The
tile&#x27;s left accent stripe color tracks the worst status, so a FAIL
tile is unmistakable from across the room.

Inside the TUI, press `r` to refresh immediately or `q` to quit.

Two modes:

- **Local** (default): runs the checks itself. Whether alerts fire
  is decided at launch via `--alerts` (off by default - the &quot;TUI is
  enough, do not page anyone&quot; case).
- **Connect** (`--connect URL`): polls a remote `chap-checker serve`
  daemon&#x27;s `/api/state` endpoint. No local config or check loop;
  the laptop becomes a thin client of the TV (or wherever `serve`
  is running). Alerts stay where the daemon is.

**Usage**:

```console
$ chap-checker tui [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--interval FLOAT RANGE`: Refresh interval in seconds.  [default: 30.0; x&gt;=2.0]
* `--alerts / --no-alerts`: Dispatch alerts (Slack, webhook, ...) from refresh cycles. Off by default - the TUI is usually all you need; flip this on if you want it to also page.  [default: no-alerts]
* `--state PATH`: State file path (default: ./chap-checker.state.json next to the config).  [env var: CHAP_CHECKER_STATE]
* `--connect TEXT`: Render a remote `chap-checker serve` daemon instead of running checks locally. Pass the base URL (e.g. http://tv-host:8765). Mutually exclusive with --config / --state / --alerts.
* `--token TEXT`: Bearer token for an authenticated remote `chap-checker serve` (--connect mode only). Discouraged inline - the value lands in shell history and `ps` output; prefer --token-env.  [env var: CHAP_CHECKER_TOKEN]
* `--token-env TEXT`: Name of the env var holding the bearer token for --connect. Recommended over --token. Only meaningful with --connect; ignored otherwise.
* `--help`: Show this message and exit.

## `chap-checker serve`

Launch the chap-checker server.

Long-running daemon. Runs the check loop, dispatches alerts (when
`--alerts`), exposes JSON state at `/api/state`, and by default
serves a browser dashboard at `/`. The TUI&#x27;s `--connect` mode and
any browser pointed at this server consume the same snapshot.

Designed to be `systemd`-supervised on a TV machine or small VM and
left running - see the [Server guide](https://dhis2-chap.github.io/chap-checker/guides/serve/)
for the unit file.

Pass `--no-ui` for an API-only daemon; the browser dashboard is
skipped and `/` returns 404, but everything under `/api/*` is
unchanged.

**Usage**:

```console
$ chap-checker serve [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--interval FLOAT RANGE`: Server-side check refresh interval (seconds).  [default: 30.0; x&gt;=2.0]
* `--alerts / --no-alerts`: Dispatch alerts (Slack, webhook, ...) from refresh cycles. Off by default - the dashboard is usually all you need; flip this on if you want the daemon to also page.  [default: no-alerts]
* `--state PATH`: State file path (default: ./chap-checker.state.json next to the config).  [env var: CHAP_CHECKER_STATE]
* `--host TEXT`: Bind address. Use 0.0.0.0 to expose on the local network (e.g. for a TV).  [default: 127.0.0.1]
* `--port INTEGER RANGE`: Port to listen on.  [default: 8765; 1&lt;=x&lt;=65535]
* `--ui / --no-ui`: Serve the browser dashboard at `/`. Pass `--no-ui` for a headless deployment where only `chap-checker tui --connect` clients or external scrapers consume `/api/state`.  [default: ui]
* `--help`: Show this message and exit.

## `chap-checker alerts`

Inspect or test configured alerters.

**Usage**:

```console
$ chap-checker alerts [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List registered alerter types and their...
* `ls`: List registered alerter types.
* `test`: Send a synthetic transition to every...

### `chap-checker alerts list`

List registered alerter types and their TOML fields.

**Usage**:

```console
$ chap-checker alerts list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `chap-checker alerts ls`

List registered alerter types.

**Usage**:

```console
$ chap-checker alerts ls [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `chap-checker alerts test`

Send a synthetic transition to every configured alerter (or a named one).

Useful after rotating a credential (Slack webhook URL, generic webhook
bearer token, ...) or when you suspect the alert pipeline is broken.
Each invocation posts a real message / payload to the configured
receiver, so do not put this on a cron - run it manually.

`--kind` picks which direction to test: `failure` (default) sends an
OK-&gt;FAIL transition, `recovery` sends a FAIL-&gt;OK, and `both` sends
the pair so a single command exercises the full round-trip.

Exit code is 0 only when every alerter delivered successfully.

**Usage**:

```console
$ chap-checker alerts test [OPTIONS]
```

**Options**:

* `-n, --name TEXT`: Send only to this alerter (must be a configured alerter name). Default: every configured alerter.
* `-k, --kind [failure|recovery|both]`: Which synthetic transition(s) to send: failure (default), recovery, or both.  [default: failure]
* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--help`: Show this message and exit.

## `chap-checker checks`

Inspect available checks.

**Usage**:

```console
$ chap-checker checks [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List registered checks.
* `ls`: List every registered check with order,...

### `chap-checker checks list`

List registered checks.

**Usage**:

```console
$ chap-checker checks list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `chap-checker checks ls`

List every registered check with order, prerequisites, and description.

**Usage**:

```console
$ chap-checker checks ls [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.
