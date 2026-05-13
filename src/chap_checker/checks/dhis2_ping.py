"""Basic reachability + authentication check against the DHIS2 server."""

from __future__ import annotations

import time
from typing import ClassVar

from chap_checker.checks.base import CheckResult, Status, register_check
from chap_checker.client import Dhis2Client


@register_check
class Dhis2PingCheck:
    """Verify that the DHIS2 server responds to ``/api/me`` with the given credentials."""

    name: ClassVar[str] = "dhis2_ping"
    description: ClassVar[str] = "DHIS2 server reachable and credentials accepted."
    order: ClassVar[int] = 10
    requires: ClassVar[list[str]] = []

    async def run(self, client: Dhis2Client) -> CheckResult:
        start = time.perf_counter()
        try:
            response = await client.get("me")
        except Exception as exc:  # noqa: BLE001 - surface any transport error as a result
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=f"Request failed: {exc}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code == 401:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Authentication rejected (401).",
                duration_ms=duration_ms,
            )
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"Unexpected status {response.status_code}.",
                duration_ms=duration_ms,
            )

        # A 2xx HTML page (typical SSO / reverse-proxy login interception)
        # is NOT proof of DHIS2 auth - reject it.
        if not response.headers.get("content-type", "").startswith("application/json"):
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=(
                    "/api/me responded with a non-JSON body - likely an SSO or proxy login page "
                    "intercepted the request. The DHIS2 server itself was not reached."
                ),
                duration_ms=duration_ms,
            )
        try:
            body = response.json()
        except ValueError:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Server returned malformed JSON for /api/me.",
                duration_ms=duration_ms,
            )
        if not isinstance(body, dict):
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Unexpected /api/me response shape (expected a JSON object).",
                duration_ms=duration_ms,
            )
        # Real DHIS2 always populates at least one of `username` or `id`.
        # Anything else is some proxy or stub responding with valid JSON but
        # without the credentials having been honored.
        username = body.get("username")
        user_id = body.get("id")
        if not username and not user_id:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=("/api/me returned JSON with neither 'username' nor 'id' - the response is not from DHIS2."),
                duration_ms=duration_ms,
            )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"Authenticated as {username or user_id}.",
            details={"username": username, "user_id": user_id},
            duration_ms=duration_ms,
        )
