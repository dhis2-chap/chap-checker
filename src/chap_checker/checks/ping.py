"""Basic reachability + authentication check."""

from __future__ import annotations

import time

from chap_checker.checks.base import CheckResult, Status, register
from chap_checker.client import Dhis2Client


class PingCheck:
    """Verify that the DHIS2 server responds to ``/api/me`` with the given credentials."""

    name = "ping"
    description = "Server reachable and credentials accepted."
    order = 10
    requires: list[str] = []

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

        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"Authenticated as {body.get('username', '?')}.",
            details={"username": body.get("username"), "user_id": body.get("id")},
            duration_ms=duration_ms,
        )


register(PingCheck())
