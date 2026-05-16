"""Check that the climate app is installed on DHIS2."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from dhis2w_client import Dhis2Client

from chap_checker.checks.base import CheckContext, CheckResult, Status, format_request_error, register_check
from chap_checker.checks.dhis2_chap_modeling_app import Dhis2App

CLIMATE_APP_HUB_ID = "effb986c-a3c7-485e-a2f6-5e54ff9df7c3"
APPS_PATH = "/api/apps"


@register_check
class Dhis2ChapClimateAppCheck:
    """Look for an installed DHIS2 app whose App Hub UUID matches the climate app."""

    name: ClassVar[str] = "dhis2_chap_climate_app"
    description: ClassVar[str] = "DHIS2 app with app_hub_id of the climate app is installed and reports a version."
    order: ClassVar[int] = 70
    # See `dhis2_chap_modeling_app.requires` for the rationale: depending
    # on the chap route gates this check so plain DHIS2 instances skip it
    # cleanly instead of FAIL-ing on every poll.
    requires: ClassVar[list[str]] = ["dhis2_chap_route"]

    async def run(self, client: Dhis2Client, ctx: CheckContext) -> CheckResult:  # noqa: ARG002
        start = time.perf_counter()
        try:
            response = await client.get_response(APPS_PATH)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=self.name,
                status=Status.ERROR,
                message=format_request_error(exc, path=APPS_PATH),
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if response.status_code >= 400:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"Could not list apps (status {response.status_code}).",
                duration_ms=duration_ms,
            )

        try:
            body = response.json()
        except ValueError:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="DHIS2 returned malformed JSON when listing apps.",
                duration_ms=duration_ms,
            )
        if isinstance(body, list):
            entries = body
        elif isinstance(body, dict):
            entries = body.get("apps", [])
        else:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Unexpected /api/apps response shape (expected a list or object).",
                duration_ms=duration_ms,
            )
        if not all(isinstance(e, dict) for e in entries):
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message="Unexpected /api/apps entry shape (entries must be objects).",
                duration_ms=duration_ms,
            )
        apps = [Dhis2App.model_validate(entry) for entry in entries]
        match = next((a for a in apps if a.app_hub_id == CLIMATE_APP_HUB_ID), None)
        if match is None:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"No app with app_hub_id '{CLIMATE_APP_HUB_ID}' installed.",
                details={"app_count": len(apps)},
                duration_ms=duration_ms,
            )
        label = match.name or match.key or "climate-app"
        if not match.version:
            return CheckResult(
                name=self.name,
                status=Status.WARN,
                message=f"{label} installed but no 'version' field.",
                details=_dump(match),
                duration_ms=duration_ms,
            )
        return CheckResult(
            name=self.name,
            status=Status.OK,
            message=f"{label} {match.version}.",
            details=_dump(match),
            duration_ms=duration_ms,
        )


def _dump(app: Dhis2App) -> dict[str, Any]:
    return app.model_dump(exclude_none=True, by_alias=True)
