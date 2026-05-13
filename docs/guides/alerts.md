# Alerting (Slack)

Alerts are stateful and transition-only: a sustained outage produces one
Slack message on entry and one on recovery, not a ping every cron tick.

## Two-step setup

### 1. Configure the transport

Add an `[alerts.<name>]` block. Currently only `slack` is supported.

```toml
[alerts.slack]
webhook_url_env = "SLACK_WEBHOOK_URL"
# webhook_url = "https://hooks.slack.com/services/..."   # alternative
# notify_on = ["fail", "error", "warn"]                  # default
# timeout_s = 10.0
```

### 2. Opt instances in

`alerts = [...]` on an instance lists which alerters fire for it. Default is
`[]` — no opt-in, no Slack.

```toml
[instances.prod]               # pages on transitions
url = "https://dhis2.example.com"
alerts = ["slack"]
# ...

[instances.staging]            # silent, tracked but never paged
url = "https://staging.dhis2.example.com"
# no `alerts =` line
# ...
```

List configured alerters at runtime:

```bash
chap-checker alerts list             # Rich table
chap-checker --json alerts list      # JSON for tooling
```

## Creating the Slack webhook

1. Open <https://api.slack.com/apps> and **Create New App → From scratch** (or
   reuse an existing app).
2. In the app, open **Features → Incoming Webhooks** and toggle activation on.
3. Click **Add New Webhook to Workspace** and pick the channel the alerts
   should land in.
4. Copy the generated URL — it looks like
   `https://hooks.slack.com/services/T.../B.../...`.
5. Put it in `chap-checker.toml` either inline as `webhook_url` or, preferred,
   set `webhook_url_env = "SLACK_WEBHOOK_URL"` and export the URL in the
   environment / your secrets manager.

Treat the webhook URL as a credential: anyone who has it can post to the
channel. Slack's full guide: <https://api.slack.com/messaging/webhooks>.

## Testing the wiring

Verify the webhook without breaking a real instance:

```bash
chap-checker alerts test                       # send through every configured alerter
chap-checker alerts test --name slack          # send through one
chap-checker --json alerts test                # parseable AlertTestReport JSON
```

The command posts a synthetic FAIL→OK transition to each configured alerter.
Exit code reflects per-alerter delivery success.

!!! warning
    `alerts test` posts a **real** message to the configured channel.
    Run it manually after webhook rotation, not on a cron.

## Transition semantics

State is persisted to `./chap-checker.state.json` (override with
`--state <path>`). Every run computes the diff against the saved state and
decides whether to dispatch.

| Previous     | Current      | Fires?       |
| ------------ | ------------ | ------------ |
| OK           | FAIL / ERROR / WARN | yes (failure) |
| FAIL / ERROR / WARN | OK    | yes (recovery) |
| FAIL ↔ ERROR ↔ WARN | (intra-failure flip) | **no** |
| any          | SKIPPED      | **no** (SKIPPED never persists) |
| SKIPPED      | any          | depends on the *previous real* status |

The "two-sided guard": at least one side of the transition must be `OK`,
otherwise we're just relabeling an ongoing outage.

The "skip-through-SKIPPED" rule: `SKIPPED` results are not written to the
state file. If `dhis2_chap_route` was `FAIL`, then upstream `dhis2_ping`
flapped and made it `SKIPPED`, then everything recovered and it's `OK` again,
the FAIL→OK transition still fires (the state remembered FAIL across the
SKIPPED window).

## Delivery failure retry

If an alerter throws (Slack 5xx, transport error), the dispatcher swallows
the exception so it can't change the run's exit code — **but it does not
save the new state**. The next run recomputes the same transition and
retries. Operationally this means: a Slack outage during a real failure
produces one alert on the next cron tick after Slack recovers, not zero
alerts.

## State file

Schema (you should rarely need to look at this):

```json
{
  "version": 1,
  "states": {
    "prod::dhis2_ping": { "status": "ok", "since": "2026-05-13T15:00:00Z" },
    "prod::dhis2_chap_route": { "status": "fail", "since": "2026-05-13T15:05:12Z" }
  }
}
```

Corrupt or schema-mismatched files log a warning and are treated as empty —
alert bookkeeping must not fail a cron run that is otherwise fine. The file
is left on disk for human inspection.

The parent directory is created on save (`mkdir -p`) so `--state
/var/lib/chap-checker/state.json` on a fresh host doesn't crash dispatch.
Concurrent writes use unique tmp files via `tempfile.mkstemp` so overlapping
runs don't race on `os.replace`.

## Message format

Each Slack post is a Block Kit message wrapped in a legacy attachment with a
colored left border so the channel reader sees red / yellow / green at a
glance:

- **FAIL / ERROR** → red `#E01E5A`
- **WARN** → yellow `#ECB22E`
- **recovery (current OK)** → green `#2EB67D`

The header summarizes the run ("chap-checker: 2 new failures, 1 recovery"),
followed by one attachment per transition with the target name, URL, check
name, status, and message.
