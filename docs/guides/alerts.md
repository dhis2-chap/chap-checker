# Alerting

Alerts are stateful and transition-only: a sustained outage produces one
message on entry and one on recovery, not a ping every cron tick.

Built-in transports:

| Name | Transport | Use when |
|---|---|---|
| `slack` | Slack Incoming Webhook (Block Kit + colored attachments) | Slack-native ops chat |
| `webhook` | Generic HTTP POST with a canonical JSON envelope | Anything else: PagerDuty Events, OpsGenie, internal incident bus, custom receiver |

Run `chap-checker alerts list` for a copy-paste TOML snippet for each alerter, with inline comments explaining every field. Run `chap-checker alerts test` to fire a synthetic transition through each configured alerter without waiting for a real outage.

## Two-step setup

### 1. Configure the transport

Add one `[alerts.<name>]` block per transport you want.

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

List registered alerter types with copy-paste TOML snippets:

```bash
chap-checker alerts list             # one panel per alerter, with per-field comments
chap-checker --json alerts list      # JSON for tooling
```

Each alerter ships its own snippet (the `toml_example` ClassVar on the class), so adding a new alerter is one class + one snippet — no doc-generation step.

## Generic webhook

Use this for any receiver that accepts an `application/json` POST: PagerDuty Events, OpsGenie, an internal incident bus, n8n / Make automations, a custom Flask endpoint, etc.

```toml
[alerts.webhook]
url_env = "MY_WEBHOOK_URL"                    # or url = "https://..." (exactly one)
notify_on = ["fail", "error", "warn"]
timeout_s = 10.0
headers = { "Authorization" = "Bearer ..." }  # optional literal HTTP headers
```

### HTTP request shape

```
POST {url}
Content-Type: application/json
Authorization: <whatever headers you set>   # only if `headers = {...}`

<JSON body, see below>
```

The alerter raises (and the dispatcher logs + skips the state save, so the transition is retried next run) when the response is `>= 400`. Anything else, including `2xx` with an unexpected body, is treated as delivered.

### Canonical JSON envelope (stable across versions)

Every POST carries this top-level shape:

| Field             | Type                                | Notes |
|---|---|---|
| `checker_version` | string                              | Sender's version, e.g. `"0.6.0"`. Use this to gate against future schema changes. |
| `summary.failures`   | integer                          | Count of transitions with `kind == "failure"` in this batch. |
| `summary.recoveries` | integer                          | Count of transitions with `kind == "recovery"` in this batch. |
| `transitions`     | array of [`Transition`](#transition-object) | One entry per status flip. Length is always `summary.failures + summary.recoveries`. |

A single POST may carry multiple transitions when several instances flip in the same refresh cycle. Receivers should iterate `transitions` rather than assuming `length == 1`.

### Transition object

Every entry under `transitions[]` looks like this:

| Field             | Type                                                            | Notes |
|---|---|---|
| `kind`            | `"failure"` \| `"recovery"`                                    | `failure` = OK → non-OK; `recovery` = non-OK → OK. |
| `target_name`     | string                                                          | The `[instances.<name>]` key from the config. |
| `target_url`      | string                                                          | The DHIS2 base URL of the affected instance. |
| `check_name`      | string                                                          | Check that flipped, e.g. `dhis2_ping`, `dhis2_chap_route`. Run `chap-checker checks list` for the full set. |
| `previous_status` | `"ok"` \| `"warn"` \| `"fail"` \| `"error"` \| `"skipped"`     | Status before the flip. |
| `current_status`  | same enum                                                       | Status after the flip. |
| `message`         | string                                                          | Operator-facing detail from the check (e.g. `"DHIS2 route returned 502 - chap-core did not respond."`). |
| `duration_ms`     | number                                                          | How long the check took during the run that detected the flip. |
| `occurred_at`     | string (ISO-8601, UTC)                                          | Timestamp the runner detected the flip. |

### Example — failure transition

A single check on a single target flipped from OK to FAIL:

```json
{
  "checker_version": "0.6.0",
  "summary": { "failures": 1, "recoveries": 0 },
  "transitions": [
    {
      "kind": "failure",
      "target_name": "prod",
      "target_url": "https://dhis2.example.com",
      "check_name": "dhis2_chap_ping",
      "previous_status": "ok",
      "current_status": "fail",
      "message": "DHIS2 route returned 502 - chap-core did not respond.",
      "duration_ms": 123.4,
      "occurred_at": "2026-05-16T11:30:00Z"
    }
  ]
}
```

### Example — recovery transition

The same check coming back to OK on the next refresh:

```json
{
  "checker_version": "0.6.0",
  "summary": { "failures": 0, "recoveries": 1 },
  "transitions": [
    {
      "kind": "recovery",
      "target_name": "prod",
      "target_url": "https://dhis2.example.com",
      "check_name": "dhis2_chap_ping",
      "previous_status": "fail",
      "current_status": "ok",
      "message": "chap-core responded (status 200).",
      "duration_ms": 87.1,
      "occurred_at": "2026-05-16T11:35:00Z"
    }
  ]
}
```

### Example — mixed batch (multiple transitions in one POST)

Two instances flipping in the same refresh tick share one envelope:

```json
{
  "checker_version": "0.6.0",
  "summary": { "failures": 1, "recoveries": 1 },
  "transitions": [
    {
      "kind": "failure",
      "target_name": "staging",
      "target_url": "https://staging.dhis2.example.com",
      "check_name": "dhis2_ping",
      "previous_status": "ok",
      "current_status": "fail",
      "message": "Authentication rejected (401) on /api/me - credentials no longer valid.",
      "duration_ms": 540.0,
      "occurred_at": "2026-05-16T11:30:02Z"
    },
    {
      "kind": "recovery",
      "target_name": "prod",
      "target_url": "https://dhis2.example.com",
      "check_name": "dhis2_chap_route",
      "previous_status": "fail",
      "current_status": "ok",
      "message": "DHIS2 route 'chap' is enabled.",
      "duration_ms": 92.5,
      "occurred_at": "2026-05-16T11:30:02Z"
    }
  ]
}
```

### Trying it locally

Spin up a one-shot receiver and point chap-checker at it. Python's
stdlib `http.server` doesn't accept POST requests (you'd get back a
501 `Unsupported method ('POST')`), so use a tiny dedicated handler:

```bash
# Terminal A: prints the body to stdout, replies 204. Ctrl+C to stop.
python3 -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('content-length', 0))
        print(self.path, self.rfile.read(n).decode(), flush=True)
        self.send_response(204); self.end_headers()
HTTPServer(('127.0.0.1', 9999), H).serve_forever()
"
# (or use https://webhook.site for a shareable URL.)

# Terminal B: minimal config
cat > /tmp/wh.toml <<'EOF'
[instances.play]
url = "https://play.im.dhis2.org/dev"
username = "admin"
password = "district"
alerts = ["webhook"]
checks = ["dhis2_ping", "dhis2_system_info"]

[alerts.webhook]
url = "http://127.0.0.1:9999/notify"
notify_on = ["fail", "error", "warn"]
EOF
chmod 600 /tmp/wh.toml

# Fire a synthetic round-trip without waiting for a real outage.
chap-checker alerts test --config /tmp/wh.toml --kind both
```

### Custom payload shape

If the receiver wants a different body (Slack Block Kit, Teams Adaptive Card, PagerDuty Events v2, your own JSON contract), subclass `WebhookAlerter` and override `_build_payload(transitions) -> dict`. The HTTP transport, timeout, header handling, and `>= 400` raise behavior come from the base — that's exactly how `SlackAlerter` is implemented.

```python
# my_alerter.py
from chap_checker.alerts.base import Transition, register_alerter
from chap_checker.alerts.webhook import WebhookAlerter
from typing import Any, ClassVar


@register_alerter("pagerduty")
class PagerDutyEventsAlerter(WebhookAlerter):
    name: ClassVar[str] = "pagerduty"
    description: ClassVar[str] = "PagerDuty Events API v2 - one event per transition."

    def _build_payload(self, transitions: list[Transition]) -> dict[str, Any]:
        # PagerDuty wants one POST per event; this example only emits the
        # first transition. Real code would call notify() in a loop, or
        # adapt the base to support multiple POSTs per notify call.
        t = transitions[0]
        return {
            "routing_key": "<your-integration-key>",
            "event_action": "trigger" if t.kind == "failure" else "resolve",
            "dedup_key": f"{t.target_name}:{t.check_name}",
            "payload": {
                "summary": f"{t.target_name} {t.check_name}: {t.message}",
                "source": t.target_url,
                "severity": "error" if t.kind == "failure" else "info",
            },
        }
```

### Auth and credentials

Any literal headers (bearer tokens, basic-auth, API keys) go in the `headers` dict. Keep the config `chmod 0600` since values are plaintext for now. Env-var substitution per header (e.g. `Authorization = "env:WEBHOOK_TOKEN"`) is a planned follow-up — until then, either:

- Put the token in the file and rely on filesystem permissions, or
- Subclass `WebhookAlerter` and read the token from `os.environ` inside `__init__`.

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

The command posts a synthetic transition to each configured alerter. The
default `--kind failure` posts an OK→FAIL; `--kind recovery` posts a
FAIL→OK; `--kind both` sends both transitions. Exit code reflects
per-alerter delivery success.

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
