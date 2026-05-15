"""Tests for ``dhis2_chap_climate_app`` check.

Covers the happy path (climate app found with a version), the WARN
path (found but no version), and the FAIL paths (not installed, HTTP
error). Shape-guard tests live in ``test_check_response_guards.py``
because they share the modeling-app guard surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
from pydantic import HttpUrl

from chap_checker.checks.base import Status
from chap_checker.checks.dhis2_chap_climate_app import (
    CLIMATE_APP_HUB_ID,
    Dhis2ChapClimateAppCheck,
)
from chap_checker.client import Dhis2Client, Dhis2Target

OTHER_APP_HUB_ID = "11111111-2222-3333-4444-555555555555"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Dhis2Client:
    target = Dhis2Target(
        base_url=cast(HttpUrl, "https://x.example"),
        username="u",
        password="p",
    )
    client = Dhis2Client(target)
    client._inner._http = httpx.AsyncClient(
        base_url=str(target.base_url).rstrip("/"),
        timeout=target.timeout_s,
        transport=httpx.MockTransport(handler),
    )
    return client


def _run(handler: Callable[[httpx.Request], httpx.Response]) -> object:
    async def go() -> object:
        async with _client(handler) as c:
            return await Dhis2ChapClimateAppCheck().run(c)

    return asyncio.run(go())


def test_ok_when_climate_app_present_with_version() -> None:
    """An installed climate app with a populated version is OK."""
    result = _run(
        lambda _r: httpx.Response(
            200,
            json={
                "apps": [
                    {"name": "Other App", "appHubId": OTHER_APP_HUB_ID, "version": "9.9.9"},
                    {"name": "Climate", "appHubId": CLIMATE_APP_HUB_ID, "version": "1.2.3"},
                ]
            },
        )
    )
    assert result.status is Status.OK  # type: ignore[attr-defined]
    assert "Climate" in result.message  # type: ignore[attr-defined]
    assert "1.2.3" in result.message  # type: ignore[attr-defined]


def test_ok_when_response_is_bare_list() -> None:
    """DHIS2 versions that return a top-level list (no `apps` wrapper) still parse."""
    result = _run(
        lambda _r: httpx.Response(
            200,
            json=[{"name": "Climate", "app_hub_id": CLIMATE_APP_HUB_ID, "version": "2.0.0"}],
        )
    )
    assert result.status is Status.OK  # type: ignore[attr-defined]
    assert "2.0.0" in result.message  # type: ignore[attr-defined]


def test_warn_when_climate_app_has_no_version() -> None:
    """Matching app without a `version` field warns instead of failing."""
    result = _run(
        lambda _r: httpx.Response(
            200,
            json={"apps": [{"name": "Climate", "appHubId": CLIMATE_APP_HUB_ID}]},
        )
    )
    assert result.status is Status.WARN  # type: ignore[attr-defined]
    assert "no 'version'" in result.message  # type: ignore[attr-defined]


def test_fail_when_climate_app_missing() -> None:
    """An /api/apps response without the climate UUID is a FAIL."""
    result = _run(
        lambda _r: httpx.Response(
            200,
            json={"apps": [{"name": "Other", "appHubId": OTHER_APP_HUB_ID, "version": "1.0.0"}]},
        )
    )
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert CLIMATE_APP_HUB_ID in result.message  # type: ignore[attr-defined]


def test_fail_when_apps_endpoint_returns_4xx() -> None:
    """A 4xx from `/api/apps` surfaces as FAIL, not an exception."""
    result = _run(lambda _r: httpx.Response(403, json={"message": "forbidden"}))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "403" in result.message  # type: ignore[attr-defined]
