"""Tests that checks return clean FAIL on bad response shapes / non-JSON 2xx."""

import asyncio
from collections.abc import Callable
from typing import cast

import httpx
import pytest
from dhis2w_client import Dhis2Client
from pydantic import HttpUrl

from chap_checker.checks.base import CheckContext, Status
from chap_checker.checks.dhis2_chap_modeling_app import Dhis2ChapModelingAppCheck
from chap_checker.checks.dhis2_chap_ping import Dhis2ChapPingCheck
from chap_checker.checks.dhis2_chap_system_info import Dhis2ChapSystemInfoCheck
from chap_checker.checks.dhis2_ping import Dhis2PingCheck
from chap_checker.checks.dhis2_system_info import Dhis2SystemInfoCheck
from chap_checker.client import Dhis2Target


def _target() -> Dhis2Target:
    return Dhis2Target(
        base_url=cast(HttpUrl, "https://x.example"),
        username="u",
        password="p",
    )


def _ctx() -> CheckContext:
    return CheckContext(target=_target())


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Dhis2Client:
    target = _target()
    client = target.open()
    # Preset the upstream client's HTTP pool with a MockTransport-backed
    # AsyncClient. The upstream `connect()` only constructs a fresh pool
    # when `_http is None`, so seeding it here keeps `connect()` from
    # touching the network and routes every request through the handler.
    # The auth header is added per-request inside the upstream client's
    # `_request`, so no `auth=` kwarg is needed on the AsyncClient.
    client._http = httpx.AsyncClient(
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
    result = asyncio.run(Dhis2PingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "non-JSON" in result.message or "SSO" in result.message


def test_ping_fails_on_json_without_username_or_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"something_else": "x"})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "username" in result.message and "id" in result.message


def test_ping_ok_on_json_with_username() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"username": "admin", "id": "abc"})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client, _ctx()))
    assert result.status is Status.OK
    assert "admin" in result.message


def test_system_info_fails_on_non_dict_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["a", "b", "c"])

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_chap_system_info_fails_on_non_dict_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="not an object")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_modeling_app_fails_on_string_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="not a list or object")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_modeling_app_fails_on_non_dict_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["this", "is", "a", "list of strings"])

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "entry" in result.message or "shape" in result.message


def test_modeling_app_fails_cleanly_on_malformed_dict_entry() -> None:
    """A dict that pydantic refuses (wrong types) becomes FAIL, not ERROR.

    Pre-0.8 the `Dhis2App.model_validate(...)` call could bubble a
    `pydantic.ValidationError` to the runner, which surfaced as a
    generic "Crashed" ERROR tile instead of a check-specific FAIL.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # Each entry is a dict (passes the isinstance guard above) but
        # has fields whose types Dhis2App won't coerce.
        return httpx.Response(200, json=[{"name": ["not", "a", "string"]}])

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_climate_app_fails_cleanly_on_malformed_dict_entry() -> None:
    """Same guard on the climate-app check."""
    from chap_checker.checks.dhis2_chap_climate_app import Dhis2ChapClimateAppCheck

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"version": {"nested": "object"}}])

    client = _client(handler)
    result = asyncio.run(Dhis2ChapClimateAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


# ---------- Endpoint permission diagnostics (401/403/404) ----------


def test_system_info_diagnoses_401_as_credential_problem() -> None:
    """401 on /api/system/info points the operator at the credentials, not the endpoint."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "401" in result.message
    assert "credentials" in result.message.lower()
    assert result.details["http_status"] == 401
    assert result.details["path"] == "/api/system/info"


def test_system_info_diagnoses_404_as_not_a_dhis2_instance() -> None:
    """404 on /api/system/info is unambiguous - this isn't a DHIS2 server."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "does not look like a DHIS2" in result.message
    assert result.details["http_status"] == 404


def test_modeling_app_diagnoses_403_as_missing_authority() -> None:
    """403 on /api/apps surfaces the required authority so the operator can grant it."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "403" in result.message
    assert "M_dhis-web-app-management" in result.message
    assert result.details["required_authority"] == "M_dhis-web-app-management"
    assert result.details["http_status"] == 403


def test_modeling_app_generic_500_keeps_status_in_details() -> None:
    """Anything past 401/403/404 still gets a generic line + structured status."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "Unexpected status 500" in result.message
    assert result.details["http_status"] == 500


def test_chap_ping_diagnoses_401_with_structured_status() -> None:
    """401 from the chap route surfaces the credential hint + http_status."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapPingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "401" in result.message
    assert "credentials" in result.message.lower()
    assert result.details["http_status"] == 401
    assert result.details["path"] == "/api/routes/chap/run/health"


def test_chap_ping_502_keeps_chapcore_message_and_http_status() -> None:
    """502 keeps its chap-core-specific message but now also lands a structured http_status."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapPingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "chap-core did not respond" in result.message
    assert result.details["http_status"] == 502


def test_chap_system_info_diagnoses_403_with_structured_status() -> None:
    """403 through the chap route surfaces the authority hint + http_status."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "403" in result.message
    assert result.details["http_status"] == 403


def test_chap_system_info_404_carries_route_specific_hint() -> None:
    """404 keeps the system-info-specific not-found message + structured status."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "404" in result.message
    assert "chap route" in result.message.lower() or "chap-core" in result.message
    assert result.details["http_status"] == 404
