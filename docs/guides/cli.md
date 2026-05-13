# CLI reference

`chap-checker` is the single entry point. Run with no args (or `--help`) for
the top-level command list.

## Global flags

These apply to every subcommand and go **before** the subcommand name:

| Flag                | Purpose                                                                 |
| ------------------- | ----------------------------------------------------------------------- |
| `--debug` / `-d`    | Verbose debug logging to stderr.                                        |
| `--json`            | Emit machine-readable JSON on stdout (cron-friendly).                  |
| `--quiet` / `-q`    | Suppress stdout entirely; exit code only.                              |
| `--version` / `-v`  | Show version and exit.                                                  |

## `verify`

Runs every registered check against one or more DHIS2 instances.

```bash
chap-checker verify                              # every [instances.*] in ./chap-checker.toml
chap-checker verify --instance prod              # just one
chap-checker verify --config /etc/chap-checker.toml
chap-checker verify --url URL -u U -p P          # ad-hoc, no config needed
```

| Flag                          | Purpose                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `--config <path>` / `-c`      | Override the default `./chap-checker.toml`. Env: `CHAP_CHECKER_CONFIG`.                                                                   |
| `--instance <name>` / `-i`    | Run only this instance from the config.                                                                                                  |
| `--url`                       | Ad-hoc DHIS2 base URL (bypasses config). Env: `DHIS2_URL`.                                                                                |
| `--username` / `-u`           | Ad-hoc username. Env: `DHIS2_USERNAME`.                                                                                                  |
| `--password` / `-p`           | Ad-hoc password. Env: `DHIS2_PASSWORD`.                                                                                                  |
| `--timeout <seconds>`         | HTTP timeout per request (ad-hoc mode). Default 10.                                                                                      |
| `--insecure`                  | Skip TLS certificate verification (ad-hoc mode).                                                                                         |
| `--check <name>` / `--checks` | Restrict to these checks for this run; transitive `requires` are pulled in. Repeat the flag for multiple. Overrides per-instance `checks`. |
| `--no-alerts` / `--no-alert`  | Skip alert dispatch on this run.                                                                                                         |
| `--state <path>`              | State file path. Env: `CHAP_CHECKER_STATE`.                                                                                              |
| `--concurrency N`             | Targets to check in parallel; overrides config. Default 5.                                                                               |

Exit code is non-zero when any check on any target is not `OK`.

### JSON output

```json
{
  "checker_version": "0.1.0",
  "started_at": "2026-05-13T15:00:00Z",
  "finished_at": "2026-05-13T15:00:05Z",
  "ok": false,
  "runs": [
    {
      "target_name": "prod",
      "target_url": "https://dhis2.example.com",
      "ok": false,
      "summary": { "ok": 3, "warn": 0, "fail": 1, "error": 0, "skipped": 1 },
      "results": [ /* one CheckResult per check */ ]
    }
  ]
}
```

## `checks`

Inspect available checks.

```bash
chap-checker checks list             # Rich table of (name, order, requires, description)
chap-checker --json checks list      # JSON for tooling
```

`ls` is a hidden alias for `list`.

## `alerts`

Inspect or test configured alerters.

```bash
chap-checker alerts list             # Rich table of [alerts.*] sections
chap-checker alerts test             # post a synthetic transition to every alerter
chap-checker alerts test --name slack
chap-checker --json alerts list
chap-checker --json alerts test
```

`alerts test` is intentionally manual — each invocation posts a real message
to the channel. Run after webhook rotation, not on a cron. See
[Alerting](alerts.md#testing-the-wiring).

## `dashboard`

Launch the [Textual TUI dashboard](dashboard.md).

```bash
chap-checker dashboard                       # alerts off (default)
chap-checker dashboard --alerts              # also dispatch Slack on transitions
chap-checker dashboard --interval 10         # refresh every 10s instead of 30
chap-checker dashboard --config /etc/chap-checker.toml
```

Keys inside the TUI: `r` refresh now, `q` quit.
