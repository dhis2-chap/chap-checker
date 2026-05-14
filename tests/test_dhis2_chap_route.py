"""Tests for ``dhis2_chap_route`` check, focused on JSON-shape resilience.

Regression for: a 200 response with an unexpected shape (list at top
level, bare string, malformed routes entries) used to raise a
``pydantic.ValidationError`` out of the check loop. The check now
returns a clean FAIL so the verify exit code and table row stay sane.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
from pydantic import HttpUrl

from chap_checker.checks.base import Status
from chap_checker.checks.dhis2_chap_route import Dhis2ChapRouteCheck
from chap_checker.client import Dhis2Client, Dhis2Target


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


def _run(handler: Callable[[httpx.Request], httpx.Response]) -> "object":
    async def go() -> "object":
        async with _client(handler) as c:
            return await Dhis2ChapRouteCheck().run(c)

    return asyncio.run(go())


def test_returns_fail_on_list_at_top_level() -> None:
    """``/api/routes`` returning a JSON array - not the expected object."""
    result = _run(lambda _r: httpx.Response(200, json=[{"id": "x"}]))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "unexpected shape" in result.message.lower()  # type: ignore[attr-defined]


def test_returns_fail_on_string_body() -> None:
    """``/api/routes`` returning a bare string."""
    result = _run(lambda _r: httpx.Response(200, json="oops"))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "unexpected shape" in result.message.lower()  # type: ignore[attr-defined]


def test_returns_fail_on_malformed_route_entry() -> None:
    """``routes`` array entry that isn't even an object."""
    result = _run(lambda _r: httpx.Response(200, json={"routes": ["not-an-object"]}))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "unexpected shape" in result.message.lower()  # type: ignore[attr-defined]


def test_returns_fail_on_malformed_json() -> None:
    """Non-JSON body still returns the existing 'malformed JSON' FAIL path."""
    result = _run(lambda _r: httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"}))
    assert result.status is Status.FAIL  # type: ignore[attr-defined]
    assert "malformed json" in result.message.lower()  # type: ignore[attr-defined]


def test_returns_ok_on_well_formed_response() -> None:
    """Sanity-check the happy path still works after the validation guard."""
    body = {"routes": [{"id": "abc", "code": "chap", "name": "chap", "url": "https://chap.example", "disabled": False}]}
    result = _run(lambda _r: httpx.Response(200, json=body))
    assert result.status is Status.OK  # type: ignore[attr-defined]
