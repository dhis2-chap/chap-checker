"""Check that the DHIS2 'modeling-app' is installed and report its version."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chap_checker.checks.base import CheckResult, Status, register
from chap_checker.client import Dhis2Client

MODELING_APP_HUB_ID = "a29851f9-82a7-4ecd-8b2c-58e0f220bc75"
APPS_PATH = "apps"


class Dhis2App(BaseModel):
    """Minimal projection of one entry in ``/api/apps``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    key: str | None = None
    name: str | None = None
    version: str | None = None
    app_hub_id: str | None = None
    app_type: str | None = Field(default=None, alias="appType")
    launch_url: str | None = Field(default=None, alias="launchUrl")


class ModelingAppCheck:
    """Look for an installed app whose App Hub UUID matches the modeling app."""

    name = "modeling-app"
    description = "DHIS2 app with app_hub_id of the modeling app is installed and reports a version."
    order = 50
    requires: list[str] = ["ping"]

    async def run(self, client: Dhis2Client) -> CheckResult:
        start = time.perf_counter()
        try:
            response = await client.get(APPS_PATH)
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
        entries = body if isinstance(body, list) else body.get("apps", [])
        apps = [Dhis2App.model_validate(entry) for entry in entries]
        match = next((a for a in apps if a.app_hub_id == MODELING_APP_HUB_ID), None)
        if match is None:
            return CheckResult(
                name=self.name,
                status=Status.FAIL,
                message=f"No app with app_hub_id '{MODELING_APP_HUB_ID}' installed.",
                details={"app_count": len(apps)},
                duration_ms=duration_ms,
            )
        label = match.name or match.key or "modeling-app"
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


register(ModelingAppCheck())
