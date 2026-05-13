# Built-in checks

Checks run in dependency order. If a prerequisite is not `OK`, dependent
checks are recorded as `SKIPPED` and don't contact the server — so a single
unreachable URL produces one alert (ping FAIL) instead of six cascading
failures.

Two namespaces:

- `dhis2_*` — probes against DHIS2 itself.
- `dhis2_chap_*` — probes against the `chap-core` service behind the DHIS2
  `chap` route.

| Check                       | Endpoint                                          | Requires             |
| --------------------------- | ------------------------------------------------- | -------------------- |
| `dhis2_ping`                | `/api/me`                                         | —                    |
| `dhis2_system_info`         | `/api/system/info`                                | `dhis2_ping`         |
| `dhis2_chap_route`          | `/api/routes?filter=code:eq:chap`                 | `dhis2_ping`         |
| `dhis2_chap_ping`           | `/api/routes/chap/run/health`                     | `dhis2_chap_route`   |
| `dhis2_chap_system_info`    | `/api/routes/chap/run/system/info` (parsed)       | `dhis2_chap_ping`    |
| `dhis2_chap_modeling_app`   | `/api/apps` (matched by `app_hub_id`)             | `dhis2_ping`         |

List them at runtime:

```bash
chap-checker checks list             # Rich table
chap-checker --json checks list      # JSON for tooling
```

## What each check does

### `dhis2_ping`

Fetches `/api/me` and verifies that:

- The response uses an `application/json` content-type (an SSO / reverse-proxy
  HTML login page would fail this).
- The body is a JSON object.
- The body has at least `username` or `id` populated.

This means a 200 from a proxy that didn't actually authenticate against DHIS2
is reported as `FAIL`, not a false `OK`.

### `dhis2_system_info`

Hits `/api/system/info` and parses the response into a pydantic model. Returns
`OK` with the DHIS2 `version` and `revision` in the result's `details`. If the
body is JSON but the `version` field is missing, `WARN`.

### `dhis2_chap_route`

Queries `/api/routes?filter=code:eq:chap` to confirm a DHIS2 route with
`code = "chap"` exists. Looks up by code (not ID) because route IDs are random
UUIDs that vary per deployment. If the route exists but `disabled = true`, the
check returns `WARN`.

### `dhis2_chap_ping`

Hits `/api/routes/chap/run/health` — chap-core's `/health` endpoint behind the
DHIS2 route. Cheap liveness check; doesn't parse the body. A 502 from the
route layer is reported as a chap-core outage.

### `dhis2_chap_system_info`

Hits `/api/routes/chap/run/system/info` and parses the chap-core response.
Surfaces `chap_core_version` (aliased to `version`), `python_version`,
`revision`, and `server_date` in `details`.

### `dhis2_chap_modeling_app`

Lists `/api/apps` and matches by `app_hub_id`
(`a29851f9-82a7-4ecd-8b2c-58e0f220bc75`). Accepts both `app_hub_id`
(snake_case) and `appHubId` (camelCase) since DHIS2 versions disagree.
Returns `OK` with the installed app's version.

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
