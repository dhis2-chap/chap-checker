"""Open Climate Service checks: ocs_health, ocs_info, and running them through the runner."""

from collections.abc import Callable
from typing import Any, cast

import httpx2 as httpx
import pytest
from pydantic import HttpUrl

from chap_checker.checks.base import CheckContext, CheckResult, Status, resolve_checks
from chap_checker.checks.ocs_health import OcsHealthCheck
from chap_checker.checks.ocs_info import OcsInfoCheck
from chap_checker.client import OcsTarget
from chap_checker.runner import run_checks

_URL = "https://ocs.example"

_CAPABILITIES = {"api_version": "1.2.0", "backend_version": "0.1.0", "id": "nepal-climate-service-demo"}
_INFO = {"app_version": "0.1.0", "python_version": "3.12", "uvicorn_version": "0.46.0", "read_only": True}

Handler = Callable[[httpx.Request], httpx.Response]


def _target() -> OcsTarget:
    return OcsTarget(base_url=cast(HttpUrl, _URL))


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_URL, transport=httpx.MockTransport(handler))


def _routes(routes: dict[str, httpx.Response]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(404))

    return handler


async def _run(check: Any, handler: Handler) -> CheckResult:
    async with _client(handler) as client:
        result: CheckResult = await check.run(client, CheckContext(target=_target()))
        return result


@pytest.mark.asyncio
async def test_health_ok() -> None:
    result = await _run(OcsHealthCheck(), _routes({"/health": httpx.Response(200, json={"status": "healthy"})}))
    assert result.status is Status.OK
    assert result.details["health_status"] == "healthy"


@pytest.mark.asyncio
async def test_health_degraded_is_warn() -> None:
    result = await _run(OcsHealthCheck(), _routes({"/health": httpx.Response(200, json={"status": "degraded"})}))
    assert result.status is Status.WARN


@pytest.mark.asyncio
async def test_health_unhealthy_is_fail() -> None:
    result = await _run(OcsHealthCheck(), _routes({"/health": httpx.Response(200, json={"status": "unhealthy"})}))
    assert result.status is Status.FAIL
    assert "unhealthy" in result.message


@pytest.mark.asyncio
async def test_health_non_json_is_fail() -> None:
    result = await _run(OcsHealthCheck(), _routes({"/health": httpx.Response(200, text="<html>login</html>")}))
    assert result.status is Status.FAIL
    assert "non-JSON" in result.message


@pytest.mark.asyncio
async def test_health_404_says_not_ocs() -> None:
    result = await _run(OcsHealthCheck(), _routes({}))
    assert result.status is Status.FAIL
    assert "does not look like an Open Climate Service" in result.message
    assert result.details["http_status"] == 404


@pytest.mark.asyncio
async def test_health_transport_error_is_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await _run(OcsHealthCheck(), boom)
    assert result.status is Status.ERROR
    assert result.message.startswith("ConnectError (/health)")


@pytest.mark.asyncio
async def test_info_reports_version_and_capabilities() -> None:
    result = await _run(
        OcsInfoCheck(),
        _routes({"/info": httpx.Response(200, json=_INFO), "/": httpx.Response(200, json=_CAPABILITIES)}),
    )
    assert result.status is Status.OK
    assert result.message == "Open Climate Service 0.1.0 (openEO 1.2.0, read-only)."
    assert result.details == {
        "version": "0.1.0",
        "api_version": "1.2.0",
        "backend_id": "nepal-climate-service-demo",
        "read_only": True,
    }


@pytest.mark.asyncio
async def test_info_falls_back_to_backend_version() -> None:
    result = await _run(
        OcsInfoCheck(),
        _routes(
            {
                "/info": httpx.Response(200, json={"read_only": False}),
                "/": httpx.Response(200, json=_CAPABILITIES),
            }
        ),
    )
    assert result.status is Status.OK
    assert result.details["version"] == "0.1.0"
    assert result.message == "Open Climate Service 0.1.0 (openEO 1.2.0)."


@pytest.mark.asyncio
async def test_info_missing_endpoint_is_fail() -> None:
    result = await _run(OcsInfoCheck(), _routes({"/": httpx.Response(200, json=_CAPABILITIES)}))
    assert result.status is Status.FAIL
    assert result.details["path"] == "/info"


@pytest.mark.asyncio
async def test_info_non_object_is_fail() -> None:
    result = await _run(
        OcsInfoCheck(),
        _routes({"/info": httpx.Response(200, json=_INFO), "/": httpx.Response(200, text="not json")}),
    )
    assert result.status is Status.FAIL
    assert "/ did not return a JSON object" in result.message


def _patch_open(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    monkeypatch.setattr(OcsTarget, "open", lambda self: _client(handler))  # noqa: ARG005


@pytest.mark.asyncio
async def test_runner_runs_only_ocs_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_open(
        monkeypatch,
        _routes(
            {
                "/health": httpx.Response(200, json={"status": "healthy"}),
                "/info": httpx.Response(200, json=_INFO),
                "/": httpx.Response(200, json=_CAPABILITIES),
            }
        ),
    )
    checks = [c for c in resolve_checks(None, kind="ocs") if c.name != "http_2xx"]
    results = await run_checks(_target(), checks)
    assert [(r.name, r.status) for r in results] == [("ocs_health", Status.OK), ("ocs_info", Status.OK)]


@pytest.mark.asyncio
async def test_runner_skips_info_when_health_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_open(monkeypatch, _routes({"/health": httpx.Response(503)}))
    results = await run_checks(_target(), resolve_checks(["ocs_info"], kind="ocs"))
    assert [(r.name, r.status) for r in results] == [("ocs_health", Status.FAIL), ("ocs_info", Status.SKIPPED)]
    assert results[1].message == "Skipped: ocs_health not OK."
