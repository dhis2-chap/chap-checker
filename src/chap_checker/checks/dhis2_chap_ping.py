"""Verify that chap-core is reachable through the DHIS2 ``chap`` route."""

from __future__ import annotations

import time
from typing import ClassVar

from chap_checker.checks.base import CheckResult, Status, register_check
from chap_checker.client import Dhis2Client

PING_PATH = "routes/chap/run/health"


@register_check
class Dhis2ChapPingCheck:
    """Confirm chap-core responds to a request through the chap route."""

    name: ClassVar[str] = "dhis2_chap_ping"
    description: ClassVar[str] = "chap-core /health reachable through the chap route."
    order: ClassVar[int] = 40
    requires: ClassVar[list[str]] = ["dhis2_chap_route"]

    async def run(self, client: Dhis2Client) -> CheckResult:
        start = time.perf_counter()
        try:
            response = await client.get(PING_PATH)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=f"Request failed: {exc}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code == 502:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="DHIS2 route returned 502 - chap-core did not respond.",
                duration_ms=duration_ms,
            )
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"Unexpected status {response.status_code} from /api/{PING_PATH}.",
                duration_ms=duration_ms,
            )

        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"chap-core responded (status {response.status_code}).",
            duration_ms=duration_ms,
        )
