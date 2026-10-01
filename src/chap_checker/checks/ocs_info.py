"""Version and capabilities check against an Open Climate Service deployment."""

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

_INFO_PATH = "/info"
_CAPABILITIES_PATH = "/"


@register_check
class OcsInfoCheck:
    """Read the OCS version from ``/info`` and the openEO capabilities from ``/``.

    ``details["version"]`` carries the service version; the dashboard shows
    it on the tile, the same way it shows the DHIS2 version for DHIS2
    instances. ``read_only`` is reported because public demo deployments run
    read-only on purpose.
    """

    name: ClassVar[str] = "ocs_info"
    description: ClassVar[str] = "Open Climate Service /info and openEO capabilities readable."
    order: ClassVar[int] = 20
    requires: ClassVar[list[str]] = ["ocs_health"]
    kinds: ClassVar[frozenset[str]] = frozenset({"ocs"})

    async def run(self, client: httpx.AsyncClient, ctx: CheckContext) -> CheckResult:  # noqa: ARG002
        start = time.perf_counter()
        info: dict[str, Any] = {}
        capabilities: dict[str, Any] = {}
        for path, sink in ((_INFO_PATH, info), (_CAPABILITIES_PATH, capabilities)):
            try:
                response = await client.get(path)
            except Exception as exc:  # noqa: BLE001 - surface any transport error as a result
                return CheckResult(
                    name=self.name,
                    status=Status.ERROR,
                    message=format_request_error(exc, path=path),
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            diagnosis = diagnose_status(response.status_code, path=path)
            if diagnosis is not None:
                message, diag_details = diagnosis
                return CheckResult(
                    name=self.name,
                    status=Status.FAIL,
                    message=message,
                    details=diag_details,
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            try:
                body: Any = response.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return CheckResult(
                    name=self.name,
                    status=Status.FAIL,
                    message=f"{path} did not return a JSON object.",
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            sink.update(body)
        duration_ms = (time.perf_counter() - start) * 1000

        version = info.get("app_version") or capabilities.get("backend_version")
        api_version = capabilities.get("api_version")
        read_only = info.get("read_only")
        details: dict[str, Any] = {
            "version": version,
            "api_version": api_version,
            "backend_id": capabilities.get("id"),
            "read_only": read_only,
        }
        qualifiers: list[str] = []
        if api_version:
            qualifiers.append(f"openEO {api_version}")
        if read_only is True:
            qualifiers.append("read-only")
        suffix = f" ({', '.join(qualifiers)})" if qualifiers else ""
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"Open Climate Service {version or 'unknown version'}{suffix}.",
            details=details,
            duration_ms=duration_ms,
        )
