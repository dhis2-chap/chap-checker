"""Checks for the chap route and the chap-core service behind it."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chap_checker.checks.base import CheckResult, Status, register
from chap_checker.client import Dhis2Client

CHAP_ROUTE_CODE = "chap"
ROUTE_LIST_PATH = "routes"
ROUTE_PROXY_PATH = f"routes/{CHAP_ROUTE_CODE}/run/system/info"


class Dhis2Route(BaseModel):
    """Minimal projection of a DHIS2 ``/api/routes`` entry."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    code: str | None = None
    name: str | None = None
    url: str | None = None
    disabled: bool | None = None


class Dhis2RouteList(BaseModel):
    """``/api/routes`` list response wrapper."""

    model_config = ConfigDict(extra="allow")

    routes: list[Dhis2Route] = Field(default_factory=list)


class ChapSystemInfo(BaseModel):
    """Subset of the chap-core ``/system/info`` response we surface."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    version: str | None = Field(default=None, alias="chap_core_version")
    python_version: str | None = None
    docker_available: bool | None = None
    revision: str | None = None
    server_date: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ChapRouteCheck:
    """Verify that a DHIS2 route with code ``chap`` is installed and enabled."""

    name = "chap-route"
    description = "DHIS2 route '/api/routes/chap' exists and is enabled."
    order = 30
    requires: list[str] = ["ping"]

    async def run(self, client: Dhis2Client) -> CheckResult:
        start = time.perf_counter()
        try:
            response = await client.get(
                ROUTE_LIST_PATH,
                params={
                    "fields": "id,code,name,url,disabled",
                    "filter": f"code:eq:{CHAP_ROUTE_CODE}",
                    "paging": "false",
                },
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=f"Request failed: {exc}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"Could not list routes (status {response.status_code}).",
                duration_ms=duration_ms,
            )

        listing = Dhis2RouteList.model_validate(response.json())
        match = next((r for r in listing.routes if r.code == CHAP_ROUTE_CODE), None)
        if match is None:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"No DHIS2 route with code '{CHAP_ROUTE_CODE}'.",
                details={"route_count": len(listing.routes)},
                duration_ms=duration_ms,
            )
        if match.disabled:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                message=f"Route '{CHAP_ROUTE_CODE}' exists but is disabled.",
                details=match.model_dump(exclude_none=True),
                duration_ms=duration_ms,
            )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"Route '{CHAP_ROUTE_CODE}' present -> {match.url or '?'}.",
            details=match.model_dump(exclude_none=True),
            duration_ms=duration_ms,
        )


class ChapCoreCheck:
    """Hit chap-core through the route and confirm it is actually running."""

    name = "chap-core"
    description = "chap-core reachable via /api/routes/chap/run/system/info and reports a version."
    order = 40
    requires: list[str] = ["chap-route"]

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

        info = ChapSystemInfo.model_validate({**body, "raw": body})
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


register(ChapRouteCheck())
register(ChapCoreCheck())
