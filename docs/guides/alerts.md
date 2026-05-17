# Alerting

Alerts are stateful and transition-only: a sustained outage produces one
message on entry and one on recovery, not a ping every cron tick. Two
built-in transports ship in the box, plus a subclassing hook for anything
else.

| Name      | Transport                                                  | Use when                                                                              |
|---        |---                                                         |---                                                                                    |
| `slack`   | Slack Incoming Webhook (Block Kit + colored attachments)   | Slack-native ops chat                                                                 |
| `webhook` | Generic HTTP POST with a canonical JSON envelope           | PagerDuty Events, OpsGenie, internal incident bus, n8n / Make, a custom receiver, ... |

Run `chap-checker alerts list` for a copy-paste TOML snippet per alerter
(the snippets live on each alerter class, so adding a new transport is
one class + one snippet — no doc-generation step). Run
`chap-checker alerts test` to fire a synthetic transition through every
configured alerter without waiting for a real outage.

## Per-instance opt-in

`alerts = [...]` on an instance lists which configured alerters fire for
it. The default is `[]` — no opt-in, no dispatch (the transition is
still tracked in the state file, so flipping the opt-in on later doesn't
backfill spurious "first failure" pings for sustained outages).

```toml
[instances.prod]                              # fans out to both alerters on every transition
url = "https://dhis2.example.com"
alerts = ["slack", "ops-webhook"]

[instances.staging]                           # silent, tracked but never paged
url = "https://staging.dhis2.example.com"
# no `alerts =` line, default is []
```

The string in `alerts = [...]` is the TOML section name, not the
transport. `alerts = ["slack-prod", "slack-staging"]` works fine if you
configure two `[alerts.slack-prod]` / `[alerts.slack-staging]` blocks
(see [Per-channel routing](#per-channel-routing-fan-out-or-split-by-channel)
below).

## Transports

### Slack

#### Setup

1. Open <https://api.slack.com/apps> and **Create New App → From scratch**
   (or reuse an existing app).
2. Open **Features → Incoming Webhooks** and toggle activation on.
3. Click **Add New Webhook to Workspace** and pick the channel the alerts
   should land in. The channel is baked into the URL Slack gives you back —
   chap-checker has no separate `channel` setting because Slack doesn't
   accept one on Incoming Webhooks.
4. Copy the generated URL — it looks like
   `https://hooks.slack.com/services/T.../B.../...`.
5. Add an `[alerts.slack]` block:

   ```toml
   [alerts.slack]
   webhook_url_env = "SLACK_WEBHOOK_URL"
   # webhook_url = "https://hooks.slack.com/services/..."   # alternative
   # notify_on = ["fail", "error", "warn"]                  # default
   # timeout_s = 10.0                                       # default
   ```

   Treat the webhook URL as a credential — anyone who has it can post to
   the channel. Prefer `webhook_url_env`; if you must use `webhook_url`
   inline, keep the config `chmod 0600`. The permission-warning loader
   nags you when it isn't.

Slack's own full guide on Incoming Webhooks lives at
<https://api.slack.com/messaging/webhooks>.

#### TOML options

| Field             | Type            | Default                       | Notes                                                                                                            |
|---                |---              |---                            |---                                                                                                               |
| `webhook_url`     | string          | —                             | Inline URL. Mutually exclusive with `webhook_url_env`.                                                           |
| `webhook_url_env` | string          | —                             | Name of an env var holding the URL. Recommended. Mutually exclusive with `webhook_url`.                          |
| `notify_on`       | list of Status  | `["fail", "error", "warn"]`   | Which target statuses fire. Drop `"warn"` if you want only hard failures to page. `"ok"` would fire on recovery. |
| `timeout_s`       | float           | `10.0`                        | HTTP timeout for the POST. Must be > 0.                                                                          |

Exactly one of `webhook_url` / `webhook_url_env` must be set. Config load
fails fast if both or neither are present.

#### Message shape

Each Slack post is a Block Kit message wrapped in a legacy attachment
with a colored left border so the channel reader sees red / yellow /
green at a glance:

| Status            | Hex       | Slack swatch |
|---                |---        |---           |
| FAIL / ERROR      | `#E01E5A` | red          |
| WARN              | `#ECB22E` | yellow       |
| recovery (now OK) | `#2EB67D` | green        |

The header summarises the run; one attachment per transition follows
with target name, URL, check name, status, and the operator-facing
message from the check. Rendered in a Slack client, a two-transition
batch looks roughly like:

```
┌─────────────────────────────────────────────────────────────────┐
│ chap-checker: 1 new failure, 1 recovery                         │
├─────────────────────────────────────────────────────────────────┤
│ ▍ FAILURE — `prod`                                              │  ← red bar
│ ▍ https://dhis2.example.com                                     │
│ ▍ `dhis2_chap_ping`  *FAIL*  DHIS2 route returned 502 -         │
│ ▍ chap-core did not respond.                                    │
├─────────────────────────────────────────────────────────────────┤
│ ▍ RECOVERY — `staging`                                          │  ← green bar
│ ▍ https://staging.dhis2.example.com                             │
│ ▍ `dhis2_ping`  *OK*  /api/me returned 200 with username "ops". │
└─────────────────────────────────────────────────────────────────┘
```

#### Per-channel routing (fan out or split by channel)

The `alerts = [...]` list takes section names, so two `[alerts.slack-*]`
blocks let a single deployment page two different channels with
different opt-in rules:

```toml
# Loud channel — fires on warn / fail / error.
[alerts.slack-ops]
webhook_url_env = "SLACK_OPS_WEBHOOK_URL"
notify_on = ["fail", "error", "warn"]

# Quiet channel — only the high-severity stuff, for the on-call rotation.
[alerts.slack-oncall]
webhook_url_env = "SLACK_ONCALL_WEBHOOK_URL"
notify_on = ["fail", "error"]
timeout_s = 5.0

[instances.prod]
url = "https://dhis2.example.com"
alerts = ["slack-ops", "slack-oncall"]

[instances.staging]
url = "https://staging.dhis2.example.com"
alerts = ["slack-ops"]                  # WARN/FAIL hit ops chat, on-call stays quiet
```

Both blocks register a `SlackAlerter`; the section name is just the
opt-in key. Same pattern works for `webhook` (e.g.
`[alerts.webhook-pagerduty]` and `[alerts.webhook-internal-bus]`).

#### Failure modes

| Symptom                                                  | Most likely cause                                                                                                                                                         |
|---                                                       |---                                                                                                                                                                        |
| `HTTP 404` from the webhook                              | Slack revoked the webhook (app deleted, channel archived, workspace token rotated). Generate a new URL and update `SLACK_WEBHOOK_URL`.                                    |
| `HTTP 429`                                               | Slack's per-webhook rate limit. The dispatcher will log the failure and retry on the next refresh tick — no special handling. If sustained, route to a less-busy channel. |
| `HTTP 500 / 502 / 503` from `hooks.slack.com`            | Slack incident. Retried on next tick (see [Delivery failure retry](#delivery-failure-retry)).                                                                             |
| `httpx.ConnectError` / `httpx.TimeoutException`          | Network egress problem from the daemon. Retried on next tick.                                                                                                             |
| `2xx` but the message never lands in the channel         | Wrong webhook URL pointing at a different channel, or the channel was archived. Recreate the webhook against the right channel.                                           |

#### Rate limits

Slack's Incoming Webhooks are rate-limited per webhook URL; the public
guidance is "roughly one message per second per channel" and burst
overshoot returns `HTTP 429` ([Slack
docs](https://api.slack.com/apis/rate-limits)). chap-checker batches
all transitions from one refresh tick into a single Block Kit message,
so under normal operation you're well below the limit — a five-instance
deployment that all flip in the same tick is still one POST. The limit
is only a concern if you set `--interval 2` *and* every instance flaps
hard every few seconds, which usually points at a different problem.

### Generic webhook

Use this for any receiver that accepts an `application/json` POST:
PagerDuty Events, OpsGenie, an internal incident bus, n8n / Make
automations, a custom Flask endpoint, etc.

#### Setup

```toml
[alerts.webhook]
url_env = "MY_WEBHOOK_URL"                    # or url = "https://..." (exactly one)
notify_on = ["fail", "error", "warn"]         # default
timeout_s = 10.0                              # default
headers = { "Authorization" = "Bearer ..." }  # optional literal HTTP headers
```

#### TOML options

| Field        | Type                     | Default                       | Notes                                                                                                                              |
|---           |---                       |---                            |---                                                                                                                                 |
| `url`        | string                   | —                             | Inline URL. Mutually exclusive with `url_env`.                                                                                     |
| `url_env`    | string                   | —                             | Name of an env var holding the URL. Recommended. Mutually exclusive with `url`.                                                    |
| `notify_on`  | list of Status           | `["fail", "error", "warn"]`   | Statuses that fire (same shape as Slack).                                                                                          |
| `timeout_s`  | float                    | `10.0`                        | HTTP timeout for the POST.                                                                                                         |
| `headers`    | table of `{name: value}` | `{}`                          | Extra HTTP headers. Typical use: `Authorization` / API-key headers. Values are literal — see [Auth and credentials](#auth-and-credentials) for the env-var caveat. |

#### HTTP request shape

```
POST {url}
Content-Type: application/json
Authorization: <whatever headers you set>   # only if `headers = {...}`

<JSON body, see below>
```

The alerter raises (and the dispatcher logs + skips the state save, so
the transition is retried next run) when the response is `>= 400`.
Anything else, including `2xx` with an unexpected body, is treated as
delivered.

#### Canonical JSON envelope (stable across versions)

Every POST carries this top-level shape:

| Field                | Type                                        | Notes                                                                                                |
|---                   |---                                          |---                                                                                                   |
| `checker_version`    | string                                      | Sender's version, e.g. `"0.8.2"`. Use this to gate against future schema changes.                    |
| `summary.failures`   | integer                                     | Count of transitions with `kind == "failure"` in this batch.                                         |
| `summary.recoveries` | integer                                     | Count of transitions with `kind == "recovery"` in this batch.                                        |
| `transitions`        | array of [`Transition`](#transition-object) | One entry per status flip. Length is always `summary.failures + summary.recoveries`.                 |

A single POST may carry multiple transitions when several instances
flip in the same refresh cycle. Receivers should iterate `transitions`
rather than assuming `length == 1`.

#### Transition object

Every entry under `transitions[]` looks like this:

| Field             | Type                                                       | Notes                                                                                                          |
|---                |---                                                         |---                                                                                                             |
| `kind`            | `"failure"` \| `"recovery"`                                | `failure` = OK → non-OK; `recovery` = non-OK → OK.                                                             |
| `target_name`     | string                                                     | The `[instances.<name>]` key from the config.                                                                  |
| `target_url`      | string                                                     | The DHIS2 base URL of the affected instance.                                                                   |
| `check_name`      | string                                                     | Check that flipped, e.g. `dhis2_ping`, `dhis2_chap_route`. Run `chap-checker checks list` for the full set.   |
| `previous_status` | `"ok"` \| `"warn"` \| `"fail"` \| `"error"` \| `"skipped"` | Status before the flip.                                                                                        |
| `current_status`  | same enum                                                  | Status after the flip.                                                                                         |
| `message`         | string                                                     | Operator-facing detail from the check (e.g. `"DHIS2 route returned 502 - chap-core did not respond."`).        |
| `duration_ms`     | number                                                     | How long the check took during the run that detected the flip.                                                 |
| `occurred_at`     | string (ISO-8601, UTC)                                     | Timestamp the runner detected the flip.                                                                        |

#### Example — failure transition

A single check on a single target flipped from OK to FAIL:

```json
{
  "checker_version": "0.8.2",
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

#### Example — recovery transition

The same check coming back to OK on the next refresh:

```json
{
  "checker_version": "0.8.2",
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

#### Example — mixed batch (multiple transitions in one POST)

Two instances flipping in the same refresh tick share one envelope:

```json
{
  "checker_version": "0.8.2",
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

#### Trying it locally

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

#### Custom payload shape

If the receiver wants a different body (Slack Block Kit, Teams Adaptive
Card, PagerDuty Events v2, your own JSON contract), subclass
`WebhookAlerter` and override `_build_payload(transitions) -> dict`. The
HTTP transport, timeout, header handling, and `>= 400` raise behavior
come from the base — that's exactly how `SlackAlerter` is implemented.

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

#### Auth and credentials

Any literal headers (bearer tokens, basic-auth, API keys) go in the
`headers` dict. Keep the config `chmod 0600` since values are plaintext
for now — the permission-warning loader nags you when they're inline
*and* the file is group/world-readable. Env-var substitution per header
(e.g. `Authorization = "env:WEBHOOK_TOKEN"`) is a planned follow-up —
until then, either:

- Put the token in the file and rely on filesystem permissions, or
- Subclass `WebhookAlerter` and read the token from `os.environ` inside
  `__init__`.

## Testing the wiring

Verify the dispatch path without waiting for a real outage:

```bash
chap-checker alerts test                       # send through every configured alerter
chap-checker alerts test --name slack          # send through one
chap-checker alerts test --kind both           # fire both an OK->FAIL and a FAIL->OK
chap-checker --json alerts test                # parseable AlertTestReport JSON
```

The command posts a synthetic transition to each configured alerter
(Slack post, webhook POST, anything custom you've wired up). The default
`--kind failure` posts an OK→FAIL; `--kind recovery` posts a FAIL→OK;
`--kind both` sends both transitions. Exit code reflects per-alerter
delivery success.

!!! warning
    `alerts test` posts **real** messages to every configured receiver
    on every invocation — Slack channels get a real ping, webhook
    endpoints get a real POST. Run it manually after credential
    rotation, not on a cron.

## Transition semantics

State is persisted to `./chap-checker.state.json` (override with
`--state <path>`). Every run computes the diff against the saved state
and decides whether to dispatch.

| Previous              | Current                | Fires?                          |
|---                    |---                     |---                              |
| OK                    | FAIL / ERROR / WARN    | yes (failure)                   |
| FAIL / ERROR / WARN   | OK                     | yes (recovery)                  |
| FAIL ↔ ERROR ↔ WARN   | (intra-failure flip)   | **no**                          |
| any                   | SKIPPED                | **no** (SKIPPED never persists) |
| SKIPPED               | any                    | depends on the *previous real* status |

The "two-sided guard": at least one side of the transition must be `OK`,
otherwise we're just relabeling an ongoing outage.

The "skip-through-SKIPPED" rule: `SKIPPED` results are not written to
the state file. If `dhis2_chap_route` was `FAIL`, then upstream
`dhis2_ping` flapped and made it `SKIPPED`, then everything recovered
and it's `OK` again, the FAIL→OK transition still fires (the state
remembered FAIL across the SKIPPED window).

## Delivery failure retry

If an alerter throws (5xx response, transport error, malformed reply),
the dispatcher swallows the exception so it can't change the run's exit
code — **but it does not save the new state**. The next run recomputes
the same transition and retries. Operationally this means: a Slack (or
webhook receiver) outage during a real failure produces one alert on
the next cron tick after the receiver recovers, not zero alerts.

The retry is per-batch, not per-alerter: if a single instance fans out
to both `slack` and `webhook` and Slack errors while the webhook
succeeds, the state isn't saved and **both** receivers see the
transition again on the next tick. Per-alerter dedupe is a deliberate
non-goal — operators running with two transports usually want
at-least-once on every transport rather than partial silence.

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

Corrupt or schema-mismatched files log a warning and are treated as
empty — alert bookkeeping must not fail a cron run that is otherwise
fine. The file is left on disk for human inspection.

The parent directory is created on save (`mkdir -p`) so `--state
/var/lib/chap-checker/state.json` on a fresh host doesn't crash
dispatch. Individual writes use unique tmp files via `tempfile.mkstemp`
so overlapping runs don't race on `os.replace`.

### Concurrency lock

The full *load → compute → dispatch → save* cycle is serialised across
processes by an `fcntl.flock` on a sidecar `<state>.lock` file. Without
the lock, two overlapping runs — an overdue cron tick colliding with a
manual `chap-checker verify`, or `tui` (local mode) and `serve --alerts`
sharing one state file — could each read the same prior state and
re-emit the same transition, duplicating every alert to every receiver.

- **Acquisition**: each runner opens the lock file `O_RDWR | O_CREAT`,
  takes an exclusive non-blocking flock, and polls with
  `asyncio.sleep(0.1)` so the daemon's event loop keeps serving HTTP
  while it waits.
- **Timeout**: 30 seconds by default. On timeout the dispatcher logs at
  WARNING and skips that tick's dispatch (state is left as-is so the
  next tick recomputes the same transitions). The check loop and the
  run's exit code are unaffected.
- **Footprint**: a zero-byte `<state>.lock` file is created next to the
  state file the first time dispatch runs. It is safe to delete when no
  chap-checker process is running.

!!! note "Windows"
    The lock uses `fcntl`, which ships only on POSIX. On Windows the
    context manager falls through to no-lock semantics — every write is
    still atomic via `os.replace`, but two concurrent dispatch
    processes on the same state file can still duplicate alerts. The
    supported pattern on Windows is to run a single dispatch process:
    either `chap-checker serve --alerts` *or* a cron-driven `verify`,
    not both against the same state file.
