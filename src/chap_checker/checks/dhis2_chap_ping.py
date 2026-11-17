"""Verify that chap-core is reachable through the DHIS2 ``chap`` route."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from dhis2w_client import Dhis2Client

from chap_checker.checks.base import (
    CheckContext,
    CheckResult,
    Status,
    diagnose_status,
    format_request_error,
    register_check,
)

PING_PATH = "/api/routes/chap/run/health"


@register_check
class Dhis2ChapPingCheck:
    """Confirm chap-core responds to a request through the chap route."""

    name: ClassVar[str] = "dhis2_chap_ping"
    description: ClassVar[str] = "chap-core /health reachable through the chap route."
    order: ClassVar[int] = 40
    requires: ClassVar[list[str]] = ["dhis2_chap_route"]

    async def run(self, client: Dhis2Client, ctx: CheckContext) -> CheckResult:  # noqa: ARG002
        start = time.perf_counter()
        try:
            response = await client.get_response(PING_PATH)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=format_request_error(exc, path=PING_PATH),
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code == 502:
            # 502 means the DHIS2 route is wired but chap-core upstream
            # didn't answer. Carry the http_status in details so JSON
            # consumers (alerts, monitoring) can route on it the same
            # way they route on 401/403/404 from the other checks.
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="DHIS2 route returned 502 - chap-core did not respond.",
                details={"http_status": 502, "path": PING_PATH},
                duration_ms=duration_ms,
            )
        diag = diagnose_status(
            response.status_code,
            path=PING_PATH,
            not_found_meaning=(
                f"{PING_PATH} returned 404 - the chap route exists but the upstream chap-core has no /health endpoint."
            ),
        )
        if diag is not None:
            message, details = diag
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=message,
                details=details,
                duration_ms=duration_ms,
            )

        return self._check_body(response, duration_ms)

    def _check_body(self, response: Any, duration_ms: float) -> CheckResult:
        """Validate a 2xx body is chap-core's `{"status", "message"}` health payload.

        A 2xx alone is not proof chap-core answered: an SSO or reverse
        proxy in front of DHIS2 (or of chap-core) can return a 200 HTML
        login page. Anything other than a JSON object carrying chap-core's
        `status` field is a FAIL.
        """

        def fail(message: str, **details: Any) -> CheckResult:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=message,
                details={"http_status": response.status_code, "path": PING_PATH, **details},
                duration_ms=duration_ms,
            )

        content_type = response.headers.get("content-type", "")
        if content_type and "json" not in content_type.lower():
            return fail(
                f"{PING_PATH} responded with a non-JSON body ({content_type.split(';')[0].strip()}) - "
                "likely an SSO or proxy login page intercepted the request. chap-core was not reached.",
                content_type=content_type,
            )
        try:
            body: Any = response.json()
        except ValueError:
            return fail(f"{PING_PATH} returned malformed JSON - the response is not from chap-core.")
        if not isinstance(body, dict) or not isinstance(body.get("status"), str):
            return fail(
                f"Unexpected {PING_PATH} response shape (expected a JSON object with a 'status' field) - "
                "the response is not from chap-core.",
            )

        health_status: str = body["status"]
        health_message = body.get("message") if isinstance(body.get("message"), str) else None
        details: dict[str, Any] = {"health_status": health_status}
        if health_message is not None:
            details["health_message"] = health_message
        if health_status != "success":
            suffix = f": {health_message}" if health_message else ""
            return fail(f"chap-core reported health status '{health_status}'{suffix}", **details)
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"chap-core healthy (status {response.status_code}).",
            details=details,
            duration_ms=duration_ms,
        )
