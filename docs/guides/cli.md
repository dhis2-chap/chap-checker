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
chap-checker verify --url URL --token-env PROD_TOKEN              # ad-hoc, PAT
chap-checker verify --url URL -u U --password-env PROD_PASSWORD   # ad-hoc, password
chap-checker verify --url URL -u U               # ad-hoc, prompts for password
```

For ad-hoc mode you can authenticate with either a **DHIS2 Personal
Access Token** (sent as `Authorization: ApiToken <value>`) or a
**username + password** (HTTP Basic). Token and password flags are
mutually exclusive.

- **Password**: resolved from `--password`, `--password-env NAME`,
  the `DHIS2_PASSWORD` env var, or a hidden TTY prompt - in that
  order. Requires `--username`.
- **Token**: resolved from `--token`, `--token-env NAME`, the
  `DHIS2_TOKEN` env var, or a hidden TTY prompt. `--username` is
  optional in token mode.

Cron / CI should use `--password-env` or `--token-env` so the secret
never lands in shell history or process listings.

Mode detection only looks at flags **you typed**: having
`DHIS2_PASSWORD` exported in the shell while you invoke
`--token-env FOO` keeps you in token mode (the ambient password is
simply ignored). If neither auth flag is on the command line and
**both** `DHIS2_TOKEN` and `DHIS2_PASSWORD` are exported, the CLI
errors out asking you to pick a mode explicitly rather than guessing.

| Flag                          | Purpose                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `--config <path>` / `-c`      | Override the default `./chap-checker.toml`. Env: `CHAP_CHECKER_CONFIG`.                                                                   |
| `--instance <name>` / `-i`    | Run only this instance from the config.                                                                                                  |
| `--url`                       | Ad-hoc DHIS2 base URL (bypasses config). Env: `DHIS2_URL`.                                                                                |
| `--username` / `-u`           | Ad-hoc username. Required with --password. Env: `DHIS2_USERNAME`.                                                                        |
| `--password-env <NAME>`       | Read the ad-hoc password from the named env var. Recommended for cron / CI.                                                              |
| `--password` / `-p`           | Ad-hoc password. Discouraged - lands in shell history and `ps`. Env: `DHIS2_PASSWORD`.                                                   |
| `--token-env <NAME>`          | Read a DHIS2 PAT from the named env var. Recommended for cron / CI.                                                                      |
| `--token`                     | DHIS2 Personal Access Token (ad-hoc mode). Discouraged inline. Env: `DHIS2_TOKEN`.                                                       |
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
