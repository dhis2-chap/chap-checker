# chap-checker

A small command-line health-check and alerting tool for DHIS2 instances that
integrate with `chap-core` via a DHIS2 route.

Operating a DHIS2 deployment with `chap-core` means several moving pieces all
have to stay healthy at once:

- the DHIS2 server itself (reachable, accepting credentials),
- the `chap` route on DHIS2 that proxies traffic to `chap-core`,
- the `chap-core` service behind that route,
- the modeling app on the DHIS2 frontend.

`chap-checker` runs one HTTP probe per piece against each DHIS2 instance it's
pointed at, rolls the results up into a non-zero exit code on any failure, and
optionally posts a Slack message when something flips between OK and broken
(transition-only - no Slack spam during a sustained outage, one recovery
alert when it comes back).

It's designed to be run by cron / a Kubernetes `CronJob` / any scheduler that
cares about exit codes and a quiet stdout when everything is fine. You can
also run it interactively for ad-hoc one-off checks.

## Built-in checks

Run in dependency order; if a prerequisite is not `OK`, dependent checks are
recorded as `SKIPPED` and don't contact the server (no cascade alerts). All
built-in checks share the `dhis2_chap_` namespace.

| Check                       | Endpoint                                  | Requires                |
| --------------------------- | ----------------------------------------- | ----------------------- |
| `dhis2_chap_ping`           | `/api/me`                                 | -                       |
| `dhis2_chap_system_info`    | `/api/system/info`                        | `dhis2_chap_ping`       |
| `dhis2_chap_route`          | `/api/routes?filter=code:eq:chap`         | `dhis2_chap_ping`       |
| `dhis2_chap_core`           | `/api/routes/chap/run/system/info`        | `dhis2_chap_route`      |
| `dhis2_chap_modeling_app`   | `/api/apps` (matched by `app_hub_id`)     | `dhis2_chap_ping`       |

List them at runtime:

```bash
chap-checker checks list           # Rich table
chap-checker --json checks list    # JSON for tooling
```

### Restricting checks per instance

By default every registered check runs. To skip parts of the stack on a given
instance, set `checks` in the TOML - the transitive `requires` of each named
check are pulled in automatically:

```toml
[instances.pure-dhis2]
url = "https://dhis2.example.com"
username = "admin"
password_env = "DHIS2_PASS"
checks = ["dhis2_chap_ping"]   # only verify auth, skip chap-related probes
```

### Adding a new check

Drop a new file under `src/chap_checker/checks/` and decorate the class:

```python
from chap_checker.checks.base import CheckResult, Status, register_check
from typing import ClassVar

@register_check
class Dhis2ChapMyCustomCheck:
    name: ClassVar[str] = "dhis2_chap_my_custom"
    description: ClassVar[str] = "what it verifies"
    order: ClassVar[int] = 60
    requires: ClassVar[list[str]] = ["dhis2_chap_ping"]

    async def run(self, client):
        ...
```

Then import the new module from `src/chap_checker/checks/__init__.py` so it
registers on package load.

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

### Creating the Slack webhook

1. Open https://api.slack.com/apps and **Create New App -> From scratch**
   (or reuse an existing app).
2. In the app, open **Features -> Incoming Webhooks** and toggle activation on.
3. Click **Add New Webhook to Workspace** and pick the channel the alerts
   should land in.
4. Copy the generated URL - it looks like
   `https://hooks.slack.com/services/T.../B.../...`.
5. Put it in `chap-checker.toml` either inline as `webhook_url` or, preferred,
   set `webhook_url_env = "SLACK_WEBHOOK_URL"` and export the URL in the
   environment / your secrets manager.

Treat the webhook URL as a credential: anyone who has it can post to the
channel. Slack's full guide: https://api.slack.com/messaging/webhooks

### Testing the wiring

Verify the webhook without breaking a real instance:

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
