"""Liveness check against an Open Climate Service deployment."""

from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx2 as httpx

from chap_checker.checks.base import (
    CheckContext,
    CheckResult,
    Status,
    diagnose_status,
    format_request_error,
    register_check,
)

_PATH = "/health"


@register_check
class OcsHealthCheck:
    """Verify that the OCS service answers ``/health`` with ``{"status": "healthy"}``.

    This is a liveness signal only: upstream ``/health`` reports the API
    process is up and does not probe storage or the job service. ``degraded``
    maps to WARN and ``unhealthy`` (or any other value) to FAIL so the check
    starts reporting more as soon as OCS does.
    """

    name: ClassVar[str] = "ocs_health"
    description: ClassVar[str] = "Open Climate Service /health reports healthy (liveness only)."
    order: ClassVar[int] = 10
    requires: ClassVar[list[str]] = []
    kinds: ClassVar[frozenset[str]] = frozenset({"ocs"})

    async def run(self, client: httpx.AsyncClient, ctx: CheckContext) -> CheckResult:  # noqa: ARG002
        start = time.perf_counter()
        try:
            response = await client.get(_PATH)
        except Exception as exc:  # noqa: BLE001 - surface any transport error as a result
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=format_request_error(exc, path=_PATH),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        duration_ms = (time.perf_counter() - start) * 1000

        diagnosis = diagnose_status(
            response.status_code,
            path=_PATH,
            not_found_meaning=f"{_PATH} returned 404 - this server does not look like an Open Climate Service.",
        )
        if diagnosis is not None:
            message, diag_details = diagnosis
            return CheckResult(
                name=self.name, status=Status.FAIL, message=message, details=diag_details, duration_ms=duration_ms
            )

        try:
            body: Any = response.json()
        except ValueError:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"{_PATH} responded with a non-JSON body - a proxy or login page may be in front of OCS.",
                duration_ms=duration_ms,
            )

        reported = str(body.get("status", "")).lower() if isinstance(body, dict) else ""
        details: dict[str, Any] = {"health_status": reported or None}
        if reported == "healthy":
            return CheckResult(
                name=self.name, status=Status.OK, message="Service healthy.", details=details, duration_ms=duration_ms
            )
        if reported == "degraded":
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                message="Service reports degraded.",
                details=details,
                duration_ms=duration_ms,
            )
        return CheckResult(
            name=self.name,
            status=Status.FAIL,
            message=f"Service reports status {reported!r}." if reported else f"{_PATH} body has no 'status' field.",
            details=details,
            duration_ms=duration_ms,
        )
