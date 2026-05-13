"""Basic reachability + authentication check against the DHIS2 server."""

from __future__ import annotations

import time
from typing import ClassVar

from chap_checker.checks.base import CheckResult, Status, register_check
from chap_checker.client import Dhis2Client


@register_check
class Dhis2ChapPingCheck:
    """Verify that the DHIS2 server responds to ``/api/me`` with the given credentials."""

    name: ClassVar[str] = "dhis2_chap_ping"
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

        body: dict[str, object] = {}
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                body = response.json() or {}
            except ValueError:
                return CheckResult(
                    name=self.name,
                    status=Status.FAIL,
                    message="Server returned malformed JSON for /api/me.",
                    duration_ms=duration_ms,
                )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"Authenticated as {body.get('username', '?')}.",
            details={"username": body.get("username"), "user_id": body.get("id")},
            duration_ms=duration_ms,
        )
