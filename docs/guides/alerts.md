# Alerting

Alerts are stateful and transition-only: a sustained outage produces one
message on entry and one on recovery, not a ping every cron tick. Two
built-in transports ship in the box; a new one is a small addition to the
code (see [Custom payload shape](#custom-payload-shape)).

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
it. The default is `[]` — no opt-in, no dispatch. The check's status is
still tracked in the state file, but an alerter only keeps open alerts
for instances opted into it: if you opt an instance in while one of its
checks is already failing, the next run sends that alerter a failure for
the ongoing outage (and later its recovery), rather than a lone recovery
for a failure it was never told about.

```toml
[instances.prod]                              # fans out to both alerters on every transition
url = "https://dhis2.example.com"
alerts = ["slack", "webhook"]

[instances.staging]                           # silent, tracked but never paged
url = "https://staging.dhis2.example.com"
# no `alerts =` line, default is []
```

The names in `alerts = [...]` are the alerter names: `slack` for the
`[alerts.slack]` block and `webhook` for `[alerts.webhook]`. Each transport
can be configured once per config file (see
[Splitting alerts across channels](#splitting-alerts-across-channels)).

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
| `webhook_url`     | string          | —                             | Inline URL. Mutually exclusive with `webhook_url_env`. Must not contain `user:pass@` credentials.                |
| `webhook_url_env` | string          | —                             | Name of an env var holding the URL. Recommended. Mutually exclusive with `webhook_url`.                          |
| `notify_on`       | list of Status  | `["fail", "error", "warn"]`   | Which statuses open an alert. Drop `"warn"` if you want only hard failures to page. `"ok"` has no effect: a recovery is always sent for a failure this alerter announced, and never otherwise. See [Transition semantics](#transition-semantics). |
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

#### Splitting alerts across channels

Each transport has exactly one block, `[alerts.slack]` and
`[alerts.webhook]`; other names under `[alerts.*]` are rejected when the
config loads. To page two destinations with different filters, use one
of each transport, for example Slack for the loud ops channel and the
generic webhook for the on-call tool, and give each its own `notify_on`:

```toml
# Loud channel: fires on warn / fail / error.
[alerts.slack]
webhook_url_env = "SLACK_OPS_WEBHOOK_URL"
notify_on = ["fail", "error", "warn"]

# On-call tool: only the high-severity statuses.
[alerts.webhook]
url_env = "ONCALL_WEBHOOK_URL"
notify_on = ["fail", "error"]

[instances.prod]
url = "https://dhis2.example.com"
alerts = ["slack", "webhook"]

[instances.staging]
url = "https://staging.dhis2.example.com"
alerts = ["slack"]                      # ops chat only, on-call stays quiet
```

Two Slack channels with different filters need two chap-checker configs
(for example two `serve` daemons), since only one `[alerts.slack]` block
is accepted.

#### Failure modes

| Symptom                                                  | Most likely cause                                                                                                                                                         |
|---                                                       |---                                                                                                                                                                        |
| `HTTP 301 / 302 / 307 / 308`                             | The URL redirects somewhere else. Redirects are not followed, so this is a delivery failure (retried next tick). Use the final URL from the logged `Location`.            |
| `HTTP 404` from the webhook                              | Slack revoked the webhook (app deleted, channel archived, workspace token rotated). Generate a new URL and update `SLACK_WEBHOOK_URL`.                                    |
| `HTTP 429`                                               | Slack's per-webhook rate limit. The dispatcher will log the failure and retry on the next refresh tick — no special handling. If sustained, route to a less-busy channel. |
| `HTTP 500 / 502 / 503` from `hooks.slack.com`            | Slack incident. Retried on next tick (see [Delivery failure retry](#delivery-failure-retry)).                                                                             |
| `ConnectError` / `ReadTimeout` / `ConnectTimeout`         | Network egress problem from the daemon. Retried on next tick.                                                                                                             |
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
| `url`        | string                   | —                             | Inline URL. Mutually exclusive with `url_env`. Must not contain `user:pass@` credentials; use `headers` for auth.                   |
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

Only a `2xx` response counts as delivered (including `2xx` with an
unexpected body). Anything else raises, and the dispatcher logs it and
skips the state save so the transition is retried next run. That
includes `3xx`: redirects are **not** followed (a POST body isn't safely
replayable across them), so a receiver that answers `301` / `302` /
`307` / `308` never got the alert. The log line names the `Location`
header; point `url` at the final endpoint.

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
| `kind`            | `"failure"` \| `"recovery"`                                | `failure` = status entered this alerter's `notify_on` set; `recovery` = back to OK after a failure was sent.     |
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
      "message": "chap-core healthy (status 200).",
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

If the receiver wants a different body (Teams Adaptive Card, PagerDuty
Events v2, your own JSON contract), that is a new alerter in
`crates/chap-checker/src/alerts.rs`: a `Flavor` variant with its payload
function, its `[alerts.<name>]` table in `config.rs`, and an entry in
`ALERTER_TYPES` for `chap-checker alerts list`. The HTTP transport, timeout,
header handling, no-redirect rule and the error on anything but 2xx are
shared, which is how the Slack alerter is built on the webhook one.

#### Auth and credentials

Any literal headers (bearer tokens, basic-auth, API keys) go in the
`headers` dict. Keep the config `chmod 0600` since values are plaintext
for now — the permission-warning loader nags you when they're inline
*and* the file is group/world-readable. Env-var substitution per header
(e.g. `Authorization = "env:WEBHOOK_TOKEN"`) is a planned follow-up —
until then, either:

- put the token in the file and rely on filesystem permissions, or
- keep the whole URL in an env var (`url_env`) when the receiver accepts the
  token in the URL.

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
and decides, **separately for each configured alerter**, whether to
dispatch.

Each alerter has its own *alerting set*: the non-OK statuses in its
`notify_on` (`"ok"` in `notify_on` is ignored). For every check on an
instance opted into the alerter, the alerter either has an **open
alert** (it was sent a failure and no recovery yet) or not:

| Current status                         | Open alert for this alerter? | Fires?                                       |
|---                                     |---                           |---                                           |
| in the alerting set                    | no                           | yes (failure), alert opens                   |
| in the alerting set                    | yes                          | **no** (ongoing outage, e.g. FAIL → ERROR)   |
| OK                                     | yes                          | yes (recovery), alert closes                 |
| OK                                     | no                           | **no**                                       |
| non-OK, *not* in the alerting set      | yes                          | **no**, alert stays open                     |
| non-OK, *not* in the alerting set      | no                           | **no**                                       |
| SKIPPED                                | either                       | **no** (SKIPPED never persists)              |

Two guarantees follow:

- **No entry into the alerting set is hidden.** It doesn't matter where
  the check came from: with `notify_on = ["fail"]`, OK → WARN is silent
  but the following WARN → FAIL fires a failure.
- **No recovery without a failure.** A recovery only goes to an alerter
  that was sent the matching failure. OK → WARN → OK is silent end to end
  for an alerter on `["fail"]`.

A check that drops out of the alerting set into another non-OK status
(FAIL → WARN with only `fail` selected) keeps its alert open: the outage
isn't over, so nothing is sent, a later FAIL doesn't re-alert, and the
recovery fires once the check is back to OK.

Because the bookkeeping is per alerter, two alerters with different
filters see the same check history differently. With Slack on
`["fail", "error", "warn"]` and a webhook on `["fail"]`, the sequence
OK → WARN → FAIL → OK sends:

| Step        | Slack                | Webhook              |
|---          |---                   |---                   |
| OK → WARN   | failure (WARN)       | —                    |
| WARN → FAIL | —                    | failure (FAIL)       |
| FAIL → OK   | recovery             | recovery             |

An alerter that is added to the config while a check is already failing
announces that outage on its first run. An instance opted into an
alerter later behaves the same way (see
[Per-instance opt-in](#per-instance-opt-in)).

The "skip-through-SKIPPED" rule: `SKIPPED` results are not written to
the state file. If `dhis2_chap_route` was `FAIL`, then upstream
`dhis2_ping` flapped and made it `SKIPPED`, then everything recovered
and it's `OK` again, the FAIL→OK transition still fires (the state
remembered FAIL across the SKIPPED window).

## Delivery failure retry

If an alerter throws (any non-2xx response including a `3xx` redirect,
transport error, malformed reply),
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
  },
  "receivers": {
    "slack": {
      "prod::dhis2_chap_route": { "status": "fail", "since": "2026-05-13T15:05:12Z" }
    },
    "webhook": {}
  }
}
```

`states` is the last real status of every check. `receivers` lists, per
alerter, the checks it currently has an open alert for, with the status
and time the failure was sent.

State files written by older versions have no `receivers` key. They
still load: on the first run each alerter's open alerts are rebuilt from
`states` (an alert is considered open when the stored status is in that
alerter's `notify_on`), so outages in progress at upgrade time are not
re-announced and still get their recovery. Older versions reading a new
file ignore `receivers`.

Corrupt files (invalid JSON, or bytes that aren't valid UTF-8) and
schema-mismatched files log a warning and are treated as empty — alert
bookkeeping must not fail a cron run that is otherwise fine. The file is
left on disk for human inspection.

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
  takes an exclusive non-blocking lock (`flock` on Linux and macOS, the
  same lock 0.12 and earlier took, so old and new versions can share a
  state file), and retries every 0.1 s without blocking, so the daemon
  keeps serving HTTP while it waits.
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
