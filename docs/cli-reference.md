# CLI reference

Health-check CLI for DHIS2 instances integrated with chap-core. Cron-friendly with Slack alerts on status transitions and a TUI dashboard for at-a-glance monitoring.

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

* `verify`: Run every registered check against one or...
* `dashboard`: Launch the Textual TUI dashboard.
* `web`: Launch the web dashboard.
* `alerts`: Inspect or test configured alerters.
* `checks`: Inspect available checks.

## `chap-checker verify`

Run every registered check against one or more DHIS2 instances.

Source of targets is decided as follows:

1. If `--url` is given (together with `--username`), chap-checker
   runs in *ad-hoc* mode against that single URL and ignores any
   TOML config. The password is resolved in this order: explicit
   `--password`; `--password-env NAME` (recommended); the
   `DHIS2_PASSWORD` environment variable; an interactive prompt
   when stdin is a TTY.
2. Otherwise the TOML file is loaded from `--config` if given, or
   from `./chap-checker.toml` if present. Every ``
   block runs unless `--instance` narrows the run to one.

Exit code is 0 when every check on every target is `OK`, non-zero
otherwise. Alert dispatch (Slack etc.) honors each instance&#x27;s
`alerts = [...]` opt-in; skip dispatch entirely with `--no-alerts`.

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
* `--timeout FLOAT`: HTTP timeout per request (seconds, ad-hoc mode).  [default: 10.0]
* `--insecure`: Skip TLS certificate verification (ad-hoc mode).
* `--check, --checks TEXT`: Restrict to these check names (transitive `requires` are pulled in). Repeat the flag for multiple. Mirrors the per-instance `checks = [...]` config field.
* `--no-alerts, --no-alert`: Skip alert dispatch even if configured.
* `--state PATH`: Path to the persisted state file (default: ./chap-checker.state.json next to the config).  [env var: CHAP_CHECKER_STATE]
* `--concurrency INTEGER RANGE`: Number of targets to check in parallel. Overrides the config value if given. Default 5.  [1&lt;=x&lt;=100]
* `--help`: Show this message and exit.

## `chap-checker dashboard`

Launch the Textual TUI dashboard.

One tile per configured instance, in an adaptive grid (1-4 columns
depending on instance count). Each tile shows the rolled-up status,
the cumulative ping success ratio, and a per-check breakdown. The
tile&#x27;s left accent stripe color tracks the worst status, so a FAIL
tile is unmistakable from across the room.

Inside the TUI, press `r` to refresh immediately or `q` to quit.
Whether alerts fire is decided at launch via `--alerts` (off by
default - the &quot;TUI is enough, do not page anyone&quot; case).

**Usage**:

```console
$ chap-checker dashboard [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--interval FLOAT RANGE`: Refresh interval in seconds.  [default: 30.0; x&gt;=2.0]
* `--alerts / --no-alerts`: Dispatch Slack/etc. alerts from refresh cycles. Off by default - the TUI is usually all you need; flip this on if you want this dashboard to also page.  [default: no-alerts]
* `--state PATH`: State file path (default: ./chap-checker.state.json next to the config).  [env var: CHAP_CHECKER_STATE]
* `--help`: Show this message and exit.

## `chap-checker web`

Launch the web dashboard.

A single-page browser dashboard with the same tile layout and palette
as the Textual TUI. A FastAPI background task runs checks every
`--interval` seconds; the browser polls `/api/state` every few seconds
and re-renders tiles client-side.

Designed to fill a TV screen with no scrolling - pin a kiosk browser
at the URL and leave it.

**Usage**:

```console
$ chap-checker web [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--interval FLOAT RANGE`: Server-side check refresh interval (seconds).  [default: 30.0; x&gt;=2.0]
* `--alerts / --no-alerts`: Dispatch Slack/etc. alerts from refresh cycles. Off by default - the dashboard is usually all you need; flip this on if you want this dashboard to also page.  [default: no-alerts]
* `--state PATH`: State file path (default: ./chap-checker.state.json next to the config).  [env var: CHAP_CHECKER_STATE]
* `--host TEXT`: Bind address. Use 0.0.0.0 to expose on the local network (e.g. for a TV).  [default: 127.0.0.1]
* `--port INTEGER RANGE`: Port to listen on.  [default: 8765; 1&lt;=x&lt;=65535]
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

* `list`: List configured alerters.
* `ls`: List configured alerters.
* `test`: Send a synthetic transition to every...

### `chap-checker alerts list`

List configured alerters.

**Usage**:

```console
$ chap-checker alerts list [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--help`: Show this message and exit.

### `chap-checker alerts ls`

List configured alerters.

**Usage**:

```console
$ chap-checker alerts ls [OPTIONS]
```

**Options**:

* `-c, --config PATH`: Path to a TOML config (defaults to ./chap-checker.toml if present).  [env var: CHAP_CHECKER_CONFIG]
* `--help`: Show this message and exit.

### `chap-checker alerts test`

Send a synthetic transition to every configured alerter (or a named one).

Useful after rotating a Slack webhook or when you suspect the alert
pipeline is broken. Each invocation posts a real message to the
configured channel, so do not put this on a cron - run it manually.

Exit code is 0 only when every alerter delivered successfully.

**Usage**:

```console
$ chap-checker alerts test [OPTIONS]
```

**Options**:

* `-n, --name TEXT`: Send only to this alerter (must be a configured alerter name). Default: every configured alerter.
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
