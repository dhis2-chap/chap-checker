"""Check chap-core's ``/system/info`` through the DHIS2 ``chap`` route."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chap_checker.checks.base import CheckResult, Status, register_check
from chap_checker.client import Dhis2Client

ROUTE_PROXY_PATH = "routes/chap/run/system/info"


class ChapCoreSystemInfo(BaseModel):
    """Subset of the chap-core ``/system/info`` response we surface."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    version: str | None = Field(default=None, alias="chap_core_version")
    python_version: str | None = None
    docker_available: bool | None = None
    revision: str | None = None
    server_date: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


@register_check
class Dhis2ChapSystemInfoCheck:
    """Hit chap-core's system-info endpoint through the route and parse its version."""

    name: ClassVar[str] = "dhis2_chap_system_info"
    description: ClassVar[str] = "chap-core /system/info reachable via the chap route and reports a version."
    order: ClassVar[int] = 50
    requires: ClassVar[list[str]] = ["dhis2_chap_ping"]

    async def run(self, client: Dhis2Client) -> CheckResult:
        start = time.perf_counter()
        try:
            response = await client.get(ROUTE_PROXY_PATH)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=f"Request failed: {exc}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code == 404:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"/api/{ROUTE_PROXY_PATH} returned 404 (route missing or chap-core has no /system/info).",
                duration_ms=duration_ms,
            )
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"Unexpected status {response.status_code} from /api/{ROUTE_PROXY_PATH}.",
                duration_ms=duration_ms,
            )

        try:
            body = response.json()
        except ValueError:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="chap-core responded but body was not JSON.",
                duration_ms=duration_ms,
            )
        if not isinstance(body, dict):
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Unexpected chap-core /system/info response shape (expected a JSON object).",
                duration_ms=duration_ms,
            )

        info = ChapCoreSystemInfo.model_validate({**body, "raw": body})
        if not info.version:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                message="chap-core reachable but no 'version' field in response.",
                details=info.model_dump(exclude_none=True),
                duration_ms=duration_ms,
            )
        parts = [f"chap-core {info.version}"]
        if info.revision:
            parts.append(f"rev {info.revision}")
        if info.python_version:
            parts.append(f"python {info.python_version}")
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=" / ".join(parts) + ".",
            details=info.model_dump(exclude_none=True, by_alias=True, exclude={"raw"}),
            duration_ms=duration_ms,
        )
