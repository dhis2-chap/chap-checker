"""DHIS2 ``/api/system/info`` check; records server version."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from dhis2w_client import Dhis2Client
from pydantic import BaseModel, ConfigDict, Field

from chap_checker.checks.base import (
    CheckContext,
    CheckResult,
    Status,
    diagnose_status,
    format_request_error,
    parse_dhis2_version,
    register_check,
)


class Dhis2SystemInfo(BaseModel):
    """Subset of the DHIS2 ``/api/system/info`` response we surface."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    version: str | None = None
    revision: str | None = None
    build_time: str | None = Field(default=None, alias="buildTime")
    server_date: str | None = Field(default=None, alias="serverDate")
    calendar: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


@register_check
class Dhis2SystemInfoCheck:
    """Fetch ``/api/system/info`` and report DHIS2 version + revision."""

    name: ClassVar[str] = "dhis2_system_info"
    description: ClassVar[str] = "DHIS2 /api/system/info reachable and reports a version."
    order: ClassVar[int] = 20
    requires: ClassVar[list[str]] = ["dhis2_ping"]

    async def run(self, client: Dhis2Client, ctx: CheckContext) -> CheckResult:
        start = time.perf_counter()
        try:
            response = await client.get_response("/api/system/info")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=format_request_error(exc, path="/api/system/info"),
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        diag = diagnose_status(
            response.status_code,
            path="/api/system/info",
            not_found_meaning=(
                "/api/system/info returned 404 - this server does not look like a DHIS2 "
                "instance (or sits behind a proxy that doesn't expose the API)."
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

        try:
            body = response.json()
        except ValueError:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="DHIS2 responded but body was not JSON.",
                duration_ms=duration_ms,
            )
        if not isinstance(body, dict):
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Unexpected /api/system/info response shape (expected a JSON object).",
                duration_ms=duration_ms,
            )

        info = Dhis2SystemInfo.model_validate({**body, "raw": body})
        if not info.version:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                message="DHIS2 reachable but no 'version' field in response.",
                details=info.model_dump(exclude_none=True, by_alias=True),
                duration_ms=duration_ms,
            )
        # Stash the detected version on the shared run context so later
        # checks can pick a v41/v42/v43-typed payload parser. None when
        # the version string doesn't map to a supported generated module
        # (e.g. a pre-release "2.44-SNAPSHOT" on dhis2w-client 0.14, which
        # only ships v41-v43).
        ctx.dhis2_version = parse_dhis2_version(info.version)
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"DHIS2 {info.version}{f' (rev {info.revision})' if info.revision else ''}.",
            details=info.model_dump(exclude_none=True, by_alias=True, exclude={"raw"}),
            duration_ms=duration_ms,
        )
