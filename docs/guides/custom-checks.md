# Adding a custom check

Checks are tiny dataclass-style Python objects implementing a `Protocol`. To
add one:

1. Drop a new file under `src/chap_checker/checks/`.
2. Define a class with `name`, `description`, `order`, `requires`, and an
   `async def run(self, client)` method.
3. Decorate the class with `@register_check` — the decorator instantiates it
   once and adds it to the registry.
4. Import the new module from `src/chap_checker/checks/__init__.py` so the
   decorator fires on package load.

```python
from typing import ClassVar
import time

from chap_checker.checks.base import CheckResult, Status, format_request_error, register_check
from chap_checker.client import Dhis2Client


@register_check
class Dhis2ChapMyCustomCheck:
    """Verify that <something specific> behaves the way <X> expects."""

    name: ClassVar[str] = "dhis2_chap_my_custom"
    description: ClassVar[str] = "What this check verifies."
    order: ClassVar[int] = 70
    requires: ClassVar[list[str]] = ["dhis2_chap_ping"]

    async def run(self, client: Dhis2Client) -> CheckResult:
        start = time.perf_counter()
        try:
            # `client.get_response(path)` returns the raw `httpx.Response`
            # without raising on 4xx/5xx, so the check can report status
            # codes itself. `client.get(path, model=...)` is the typed
            # alternative for happy-path GETs; not what we want here.
            response = await client.get_response("/api/routes/chap/run/some/endpoint")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=format_request_error(exc, path="/api/routes/chap/run/some/endpoint"),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"Unexpected status {response.status_code}.",
                duration_ms=duration_ms,
            )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message="All good.",
            duration_ms=duration_ms,
        )
```

## Field reference

| Field         | Type             | Notes |
| ------------- | ---------------- | ----- |
| `name`        | `ClassVar[str]`  | Unique. Pick `dhis2_*` for DHIS2-level probes, `dhis2_chap_*` for chap-stack probes. The dashboard / JSON output use this name. |
| `description` | `ClassVar[str]`  | Shown in `chap-checker checks list`. |
| `order`       | `ClassVar[int]`  | Lower runs first. Built-in checks use multiples of 10 so there's room to slot custom checks between them. |
| `requires`    | `ClassVar[list[str]]` | Names of checks that must be `OK` before this one runs. If any required check is not `OK`, the runner records `SKIPPED` and never calls `run()`. |

## Conventions

- **HTTP errors return `Status.ERROR`** built via `format_request_error(exc, path=...)`.
  Plain `str(exc)` is empty for `httpx.TimeoutException`, which leaves the
  operator-facing message as just `"Request failed: "` - the helper always
  carries the exception type name and falls back to `"(no message)"` so the
  failure mode is visible at a glance.
  Bad status codes (`>= 400`) return `Status.FAIL`. Unexpected response shapes
  return `Status.FAIL`. `Status.WARN` is for "responded but with anomalies"
  (e.g. missing version field).
- **Wrap `response.json()` in `try`/`except ValueError`** — a non-JSON body
  should produce a clean `FAIL`, not a crash that the runner has to catch.
- **Guard `isinstance(body, dict)`** before constructing pydantic models from
  the response — a list / string body would otherwise raise inside the
  validator.
- **Use pydantic models** for response shapes you care about (see
  `Dhis2SystemInfo`, `ChapCoreSystemInfo`, `Dhis2App` for examples). Avoid
  `dict[str, Any]` for anything that flows further than the immediate check.
- **Use `client.get_response(path)`** for status-aware checks. It returns
  the raw `httpx.Response` without raising on 4xx/5xx, so the check can
  treat status codes as facts to report. Reserve `client.get(path, model=...)`
  for the typed happy path - it raises on 4xx/5xx and parses the body into
  a pydantic model, which is great for ingest code but the wrong shape for
  health checks. Either way, don't reach for `requests` / `urllib`.

## Registration mechanics

The `@register_check` decorator looks like this:

```python
def register_check(cls: type[Check]) -> type[Check]:
    register(cls())     # instantiate once, add to the registry
    return cls
```

There is no lazy registration — importing the module is what registers the
check. That's why `src/chap_checker/checks/__init__.py` explicitly imports
every check module. Add your import there.

## How `requires` interacts with `checks = [...]`

When a user restricts checks (per-instance `checks = ["dhis2_chap_ping"]` or
CLI `--check dhis2_chap_ping`), the runner pulls in the transitive closure of
`requires`. So declaring `requires = ["dhis2_chap_ping"]` on your custom
check means asking for `dhis2_chap_my_custom` alone still pulls in
`dhis2_ping → dhis2_chap_route → dhis2_chap_ping → dhis2_chap_my_custom`.
