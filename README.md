# chap-checker

CLI that runs a suite of checks against a DHIS2 server with `chap-core` and the
`chap` route installed.

## Built-in checks

Run in dependency order; if a prerequisite is not `OK`, dependent checks are
recorded as `SKIPPED` and don't contact the server (no cascade alerts).

| Check          | Endpoint                                  | Requires      |
| -------------- | ----------------------------------------- | ------------- |
| `ping`         | `/api/me`                                 | -             |
| `system-info`  | `/api/system/info`                        | `ping`        |
| `chap-route`   | `/api/routes?filter=code:eq:chap`         | `ping`        |
| `chap-core`    | `/api/routes/chap/run/system/info`        | `chap-route`  |
| `modeling-app` | `/api/apps` (matched by `app_hub_id`)     | `ping`        |

## Usage

Ad-hoc against a single instance:

```bash
chap-checker verify \
    --url https://dhis2.example.com \
    --username admin \
    --password REPLACE_ME
```

Or define one or more instances in `./chap-checker.toml` (see
`chap-checker.toml.example`) and run them:

```bash
chap-checker verify                          # every [instances.*]
chap-checker verify --instance prod          # just one
chap-checker verify --config /etc/chap-checker.toml
```

### Global flags

- `--debug` / `-d` - verbose logging to stderr.
- `--json` - emit machine-readable JSON instead of a Rich table.
- `--quiet` / `-q` - suppress stdout entirely; run checks, dispatch alerts,
  and exit (intended for cron).
- `--version` / `-v` - show version and exit.

### `verify` flags

- `--config <path>` / `-c` - alternative config file (env `CHAP_CHECKER_CONFIG`).
- `--instance <name>` / `-i` - run only the named instance.
- `--url`, `--username` / `-u`, `--password` / `-p` - ad-hoc mode (env
  `DHIS2_URL` / `DHIS2_USERNAME` / `DHIS2_PASSWORD`).
- `--timeout <seconds>`, `--insecure` - ad-hoc-mode HTTP knobs.
- `--no-alerts` (or `--no-alert`) - skip alert dispatch on this run.
- `--state <path>` - state file location (env `CHAP_CHECKER_STATE`).

### JSON output shape

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

Exit code is non-zero when any check on any target is not `OK`, so cron picks
up failures naturally.

## Alerting

Add an `[alerts.slack]` section to `./chap-checker.toml` (see
`chap-checker.toml.example`) and `chap-checker` will POST a Slack message
when any check's status flips between `OK` and a non-OK status.

Alerting is **stateful** and transition-only:

- Previous status of every `(instance, check)` pair is persisted to
  `./chap-checker.state.json` (override with `--state <path>`).
- A sustained outage produces one Slack message on entry and one on recovery,
  not a message every cron tick.
- Within the failure family (`FAIL` <-> `ERROR` <-> `WARN`) status flips are
  silent - only `OK` <-> failure transitions alert.
- `SKIPPED` is never persisted, so a transient upstream outage that skips a
  check doesn't erase its true status - the eventual recovery still fires.
- If an alerter (e.g. Slack) fails to deliver, state is **not** saved, so the
  next cron run recomputes the same transition and retries. Alert delivery
  failures never change the run's exit code.

Permissions: `chap-checker.toml` carries credentials; keep it `chmod 600`. The
config loader warns on POSIX if the file is group- or world-readable while
holding any inline `password` or `webhook_url`.

Verify the wiring without breaking a real instance:

```bash
chap-checker alert test                # human-readable
chap-checker --json alert test         # parseable AlertTestReport JSON
```

Exit code reflects per-alerter delivery success. Combined with cron, a daily
smoke test catches webhook URL rot.

### Cron recipes

Just signal:

```cron
*/15 * * * * chap-checker --quiet verify
```

Append structured runs to a log for ingestion:

```cron
*/15 * * * * chap-checker --json verify \
    >> /var/log/chap-checker/runs.jsonl \
    2>> /var/log/chap-checker/err.log
```

Daily alert-pipeline smoke test:

```cron
0 9 * * * chap-checker --json alert test \
    >> /var/log/chap-checker/alert-pipeline.jsonl
```

## Development

```bash
make install
make lint        # ruff format + check, mypy, pyright
make test
make coverage
```
