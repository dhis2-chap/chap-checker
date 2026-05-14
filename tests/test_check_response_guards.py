"""Tests that checks return clean FAIL on bad response shapes / non-JSON 2xx."""

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
import pytest
from pydantic import HttpUrl

from chap_checker.checks.base import Status
from chap_checker.checks.dhis2_chap_modeling_app import Dhis2ChapModelingAppCheck
from chap_checker.checks.dhis2_chap_system_info import Dhis2ChapSystemInfoCheck
from chap_checker.checks.dhis2_ping import Dhis2PingCheck
from chap_checker.checks.dhis2_system_info import Dhis2SystemInfoCheck
from chap_checker.client import Dhis2Client, Dhis2Target


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Dhis2Client:
    target = Dhis2Target(
        base_url=cast(HttpUrl, "https://x.example"),
        username="u",
        password="p",
    )
    client = Dhis2Client(target)
    # Preset the upstream client's HTTP pool with a MockTransport-backed
    # AsyncClient. The upstream `connect()` only constructs a fresh pool
    # when `_http is None`, so seeding it here keeps `connect()` from
    # touching the network and routes every request through the handler.
    # The auth header is added per-request inside the upstream client's
    # `_request`, so no `auth=` kwarg is needed on the AsyncClient.
    client._inner._http = httpx.AsyncClient(
        base_url=str(target.base_url).rstrip("/"),
        timeout=target.timeout_s,
        transport=httpx.MockTransport(handler),
    )
    return client


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/html", b"<html>SSO login</html>"),
        ("text/plain", b"hi"),
    ],
)
def test_ping_fails_on_non_json_2xx(content_type: str, body: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client))
    assert result.status is Status.FAIL
    assert "non-JSON" in result.message or "SSO" in result.message


def test_ping_fails_on_json_without_username_or_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"something_else": "x"})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client))
    assert result.status is Status.FAIL
    assert "username" in result.message and "id" in result.message


def test_ping_ok_on_json_with_username() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"username": "admin", "id": "abc"})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client))
    assert result.status is Status.OK
    assert "admin" in result.message


def test_system_info_fails_on_non_dict_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["a", "b", "c"])

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_chap_system_info_fails_on_non_dict_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="not an object")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_modeling_app_fails_on_string_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="not a list or object")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_modeling_app_fails_on_non_dict_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["this", "is", "a", "list of strings"])

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client))
    assert result.status is Status.FAIL
    assert "entry" in result.message or "shape" in result.message
