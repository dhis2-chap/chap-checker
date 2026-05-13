"""Check that a DHIS2 route with code ``chap`` is installed and enabled."""

from __future__ import annotations

import time
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from chap_checker.checks.base import CheckResult, Status, register_check
from chap_checker.client import Dhis2Client

CHAP_ROUTE_CODE = "chap"
ROUTE_LIST_PATH = "routes"


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


@register_check
class Dhis2ChapRouteCheck:
    """Verify that a DHIS2 route with code ``chap`` is installed and enabled."""

    name: ClassVar[str] = "dhis2_chap_route"
    description: ClassVar[str] = "DHIS2 route '/api/routes/chap' exists and is enabled."
    order: ClassVar[int] = 30
    requires: ClassVar[list[str]] = ["dhis2_ping"]

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

        try:
            body = response.json()
        except ValueError:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="DHIS2 returned malformed JSON when listing routes.",
                duration_ms=duration_ms,
            )
        listing = Dhis2RouteList.model_validate(body)
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
