# Built-in checks

Checks run in dependency order. If a prerequisite is not `OK`, dependent
checks are recorded as `SKIPPED` and don't contact the server — so a single
unreachable URL produces one alert (ping FAIL) instead of six cascading
failures.

Each check applies to one or more instance kinds (see
[Instance kinds](configuration.md#instance-kinds)). An instance only runs the
checks for its kind; `chap-checker checks list` shows the kinds per check.

Each kind is a plugin, and its checks share a prefix:

- `http_2xx` — a transport-level reachability probe that doesn't speak DHIS2
  (`dhis2` and `ogs` instances).
- `dhis2_*` — probes against DHIS2 itself (`kind = "dhis2"`).
- `dhis2_chap_*` — probes against the `chap-core` service behind the DHIS2
  `chap` route (`kind = "dhis2"`).
- `ogs_*` — probes against an Open Geo Service deployment (`kind = "ogs"`).
- `chap_*` — probes against chap-core reached directly (`kind = "chap"`).
- `caps_*` — probes against CAPS (`kind = "caps"`).
- `http_expect` — any URL, judged by expectations in the config
  (`kind = "http"`).

`chap-checker plugins list` shows every kind with its checks and a TOML
example.

| Check                       | Kinds        | Endpoint                                              | Requires             |
| --------------------------- | ------------ | ----------------------------------------------------- | -------------------- |
| `http_2xx`                  | dhis2, ogs   | `GET <base_url>` (unauthenticated, follows redirects) | —                    |
| `dhis2_ping`                | dhis2        | `/api/me`                                             | —                    |
| `dhis2_system_info`         | dhis2        | `/api/system/info`                                    | `dhis2_ping`         |
| `dhis2_chap_route`          | dhis2        | `/api/routes?filter=code:eq:chap`                     | `dhis2_ping`         |
| `dhis2_chap_ping`           | dhis2        | `/api/routes/chap/run/health`                         | `dhis2_chap_route`   |
| `dhis2_chap_system_info`    | dhis2        | `/api/routes/chap/run/system/info` (parsed)           | `dhis2_chap_ping`    |
| `dhis2_chap_modeling_app`   | dhis2        | `/api/apps` (matched by `app_hub_id`)                 | `dhis2_chap_route`   |
| `dhis2_chap_climate_app`    | dhis2        | `/api/apps` (matched by `app_hub_id`)                 | `dhis2_chap_route`   |
| `ogs_health`                | ogs          | `/health`                                             | —                    |
| `ogs_info`                  | ogs          | `/info` + `/` (openEO capabilities)                   | `ogs_health`         |
| `chap_health`               | chap         | `/health`                                             | —                    |
| `chap_system_info`          | chap         | `/system/info` (falls back to `/system-info`)         | `chap_health`        |
| `caps_system_info`          | caps         | `/system/info`                                        | —                    |
| `caps_chap`                 | caps         | (from `caps_system_info`: chap-core connected)        | `caps_system_info`   |
| `caps_dhis2`                | caps         | (from `caps_system_info`: DHIS2 connected)            | `caps_system_info`   |
| `http_expect`               | http         | `GET <url>` with the configured expectations          | —                    |

### Open Geo Service checks

- **`ogs_health`** is a liveness signal only. OGS's `/health` reports that the
  API process is up; it does not probe storage, the job service or the
  scheduler. `degraded` maps to `WARN`, anything other than `healthy` to
  `FAIL`, and a 404 is reported as "does not look like an Open Geo
  Service" (a common sign of a wrong URL).
- **`ogs_info`** reads the service version from `/info` (`app_version`) and the
  openEO capabilities document from `/` (`api_version`, backend `id`). The
  version feeds the dashboard tile label (`OGS 0.1.0`), and `read_only` is
  reported in the message because public demo deployments run read-only on
  purpose.

### chap-core checks (`kind = "chap"`)

For a chap-core that is reachable on its own URL, not only through DHIS2.
The URL is chap-core's base (`https://example.org/chap/stable`), without
`/health`.

- **`chap_health`** reads `/health`. The body must be chap-core's health
  payload with `status` `success`, judged by the same code as
  `dhis2_chap_ping`, so a login page or a proxy answering 200 is a `FAIL`.
- **`chap_system_info`** reads `/system/info` (or `/system-info` on chap-core
  1.1 to 1.2) and reports the version, revision and Python version. The
  version labels the instance in the dashboards (`Chap Core 2.7.0`).

### CAPS checks (`kind = "caps"`)

CAPS (Climate Automation & Prediction Scheduler) reports, at `/system/info`,
its own version and whether it reaches chap-core and DHIS2. The URL is CAPS's
base (`https://example.org/caps`).

- **`caps_system_info`** reads `/system/info` once: `OK` with the version,
  `WARN` without one, `FAIL` when the body is not CAPS's.
- **`caps_chap`** and **`caps_dhis2`** judge the two connections from that
  read (no request of their own): `OK` when connected, `FAIL` when CAPS says
  it is not, `WARN` when it doesn't say. A CAPS that is up but cut off from
  chap-core shows as exactly that, not as "CAPS is down".

### HTTP checks (`kind = "http"`)

For anything else: a landing page, an API that has no plugin, a service
behind a proxy. **`http_expect`** fetches the URL (following up to five
redirects, sending the configured `headers` and nothing else) and passes when:

- the final status is in `expect_status` (by default, any 2xx; list `401` to
  count a protected endpoint as up),
- the body contains `body_contains`, when set,
- the JSON value at `json_path` (a dotted path such as `status` or
  `data.0.state`) equals `json_equals`, when set (`"200"` matches `200`).

See [Instance kinds](configuration.md#instance-kinds) for the fields.

List them at runtime:

```bash
chap-checker checks list             # table
chap-checker --json checks list      # JSON for tooling
```

## What each check does

### `http_2xx`

Unauthenticated `GET` against the instance's configured `url`. Follows
redirects (up to five hops) and reports `OK` if the final response is in
the `2xx` range — so a DHIS2 root that returns `302` to a login page is
still `OK`. `FAIL` on any non-2xx final status, `ERROR` on transport
failure (DNS / TLS / connect).

Useful for distinguishing two failure shapes that otherwise look similar
on the tile grid:

- the reverse proxy / TLS terminator / load balancer in front of DHIS2 is
  down (`http_2xx` FAILs, `dhis2_ping` ERRORs);
- the front door is fine but the credentials are wrong or DHIS2 itself is
  unhealthy (`http_2xx` OK, `dhis2_ping` FAILs).

No `requires`, so it runs even if every other check is disabled.

### `dhis2_ping`

Fetches `/api/me` and verifies that:

- The response uses an `application/json` content-type (an SSO / reverse-proxy
  HTML login page would fail this).
- The body is a JSON object.
- The body has at least `username` or `id` populated.

This means a 200 from a proxy that didn't actually authenticate against DHIS2
is reported as `FAIL`, not a false `OK`.

### `dhis2_system_info`

Hits `/api/system/info` and reads the response. Returns
`OK` with the DHIS2 `version` and `revision` in the result's `details`. If the
body is JSON but the `version` field is missing, `WARN`.

### `dhis2_chap_route`

Queries `/api/routes?filter=code:eq:chap` to confirm a DHIS2 route with
`code = "chap"` exists. Looks up by code (not ID) because route IDs are random
UUIDs that vary per deployment. If the route exists but `disabled = true`, the
check returns `WARN`.

### `dhis2_chap_ping`

Hits `/api/routes/chap/run/health` — chap-core's `/health` endpoint behind the
DHIS2 route. Cheap liveness check. The body must be chap-core's JSON health
payload (`{"status": "success", "message": "healthy"}`): a non-JSON 2xx
(typically an SSO or reverse-proxy login page), malformed JSON, a JSON body
without a `status` field, or a `status` other than `success` is `FAIL`. A 502
from the route layer is reported as a chap-core outage.

### `dhis2_chap_system_info`

Hits `/api/routes/chap/run/system/info` and parses the chap-core response.
Surfaces `chap_core_version` (aliased to `version`), `python_version`,
`revision`, `server_date` and `auth_required` (chap-core 2.2+) in `details`.
If `/system/info` returns 404, the check retries the older
`/api/routes/chap/run/system-info` path, so chap-core 1.1.x - 1.2.0
(which predate `/system/info`) still report their version.

### `dhis2_chap_modeling_app`

Lists `/api/apps` and matches by `app_hub_id`
(`a29851f9-82a7-4ecd-8b2c-58e0f220bc75`). Accepts both `app_hub_id`
(snake_case) and `appHubId` (camelCase) since DHIS2 versions disagree.
Returns `OK` with the installed app's version.

### `dhis2_chap_climate_app`

Same shape as `dhis2_chap_modeling_app`, matched by App Hub UUID
`effb986c-a3c7-485e-a2f6-5e54ff9df7c3`. Returns `OK` with the installed
app's version. The climate app is only present on climate-data-themed
deployments — opt this check out on instances where it isn't expected
(set `checks = [...]` on the instance to exclude this name).

## Statuses

| Status    | Meaning                                                                |
| --------- | ---------------------------------------------------------------------- |
| `OK`      | The probe succeeded.                                                   |
| `WARN`    | The endpoint responded but the response was anomalous (e.g. no version field). |
| `FAIL`    | The endpoint returned an unexpected status code or body.               |
| `ERROR`   | The probe crashed (transport error, code exception).                   |
| `SKIPPED` | A prerequisite was not `OK`; the check did not contact the server.    |

Skip semantics in detail: `SKIPPED` is **informational**. It is never
persisted to the state file, so a transient upstream outage that skips a
downstream check doesn't erase its true status — the eventual recovery still
fires. See [Alerting](alerts.md#transition-semantics) for the full state
machine.
