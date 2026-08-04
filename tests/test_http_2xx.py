"""Tests for the ``http_2xx`` reachability check.

Covers the three outcome paths: 2xx OK (including via a redirect chain),
non-2xx FAIL, and transport-error ERROR. The check uses its own
``httpx.AsyncClient`` rather than going through ``Dhis2Client``, so the
mock is wired by patching ``httpx.AsyncClient`` to return a transport
backed by ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
import pytest
from pydantic import HttpUrl

from chap_checker.checks.base import CheckContext, Status
from chap_checker.checks.http_2xx import Http2xxCheck
from chap_checker.client import Dhis2Target


def _run_with_transport(handler: Callable[[httpx.Request], httpx.Response]) -> object:
    """Run the check against an in-memory MockTransport handler."""
    target = Dhis2Target(
        base_url=cast(HttpUrl, "https://x.example"),
        username="u",
        password="p",
    )
    real_async_client = httpx.AsyncClient

    def _factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs.pop("verify", None)
        return real_async_client(**kwargs)  # type: ignore[arg-type]

    async def go() -> object:
        original = httpx.AsyncClient
        httpx.AsyncClient = _factory  # type: ignore[misc, assignment]
        try:
            return await Http2xxCheck().run(cast(object, None), CheckContext(target=target))  # type: ignore[arg-type]
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]

    return asyncio.run(go())


def test_returns_ok_on_200() -> None:
    result = _run_with_transport(lambda _r: httpx.Response(200, text="hi"))
    assert result.status is Status.OK  # type: ignore[attr-defined]
    assert "200" in result.message  # type: ignore[attr-defined]
    assert result.details["http_status"] == 200  # type: ignore[attr-defined]


def test_returns_ok_after_redirect_to_2xx() -> None:
    """DHIS2's root typically 302s to a login page that returns 200; that should be OK."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("", "/"):
            return httpx.Response(302, headers={"Location": "/login"})
        return httpx.Response(200, text="login page")

    result = _run_with_transport(handler)
    assert result.status is Status.OK  # type: ignore[attr-defined]
    assert "/login" in result.details["final_url"]  # type: ignore[attr-defined]


def test_returns_fail_on_500() -> None:
    result = _run_with_transport(lambda _r: httpx.Response(500))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "500" in result.message  # type: ignore[attr-defined]


def test_returns_fail_on_4xx() -> None:
    result = _run_with_transport(lambda _r: httpx.Response(404))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "404" in result.message  # type: ignore[attr-defined]


def test_returns_error_on_transport_failure() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS failed")

    result = _run_with_transport(handler)
    assert result.status is Status.ERROR  # type: ignore[attr-defined]
    assert "ConnectError" in result.message  # type: ignore[attr-defined]


def test_registered_with_order_before_dhis2_ping() -> None:
    """http_2xx should sort before dhis2_ping so it runs first in any selection."""
    from chap_checker.checks import all_checks

    by_name = {c.name: c for c in all_checks()}
    assert "http_2xx" in by_name
    assert by_name["http_2xx"].order < by_name["dhis2_ping"].order
    assert by_name["http_2xx"].requires == []


@pytest.mark.parametrize("status_code", [201, 202, 204, 299])
def test_any_2xx_is_ok(status_code: int) -> None:
    result = _run_with_transport(lambda _r: httpx.Response(status_code))
    assert result.status is Status.OK  # type: ignore[attr-defined]
