"""Tests that checks return clean FAIL on bad response shapes / non-JSON 2xx."""

import asyncio
from collections.abc import Callable
from typing import cast

import httpx2
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


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> Dhis2Client:
    target = _target()
    client = target.open()
    # Preset the upstream client's HTTP pool with a MockTransport-backed
    # AsyncClient. The upstream `connect()` only constructs a fresh pool
    # when `_http is None`, so seeding it here keeps `connect()` from
    # touching the network and routes every request through the handler.
    # The auth header is added per-request inside the upstream client's
    # `_request`, so no `auth=` kwarg is needed on the AsyncClient.
    client._http = httpx2.AsyncClient(
        base_url=str(target.base_url).rstrip("/"),
        timeout=target.timeout_s,
        transport=httpx2.MockTransport(handler),
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
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": content_type})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "non-JSON" in result.message or "SSO" in result.message


def test_ping_fails_on_json_without_username_or_id() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"something_else": "x"})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "username" in result.message and "id" in result.message


def test_ping_ok_on_json_with_username() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"username": "admin", "id": "abc"})

    client = _client(handler)
    result = asyncio.run(Dhis2PingCheck().run(client, _ctx()))
    assert result.status is Status.OK
    assert "admin" in result.message


def test_system_info_fails_on_non_dict_body() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=["a", "b", "c"])

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_chap_system_info_fails_on_non_dict_body() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json="not an object")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_modeling_app_fails_on_string_body() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json="not a list or object")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_modeling_app_fails_on_non_dict_entries() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=["this", "is", "a", "list of strings"])

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

    def handler(request: httpx2.Request) -> httpx2.Response:
        # Each entry is a dict (passes the isinstance guard above) but
        # has fields whose types Dhis2App won't coerce.
        return httpx2.Response(200, json=[{"name": ["not", "a", "string"]}])

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


def test_climate_app_fails_cleanly_on_malformed_dict_entry() -> None:
    """Same guard on the climate-app check."""
    from chap_checker.checks.dhis2_chap_climate_app import Dhis2ChapClimateAppCheck

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[{"version": {"nested": "object"}}])

    client = _client(handler)
    result = asyncio.run(Dhis2ChapClimateAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "shape" in result.message


# ---------- Endpoint permission diagnostics (401/403/404) ----------


def test_system_info_diagnoses_401_as_credential_problem() -> None:
    """401 on /api/system/info points the operator at the credentials, not the endpoint."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"message": "Unauthorized"})

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "401" in result.message
    assert "credentials" in result.message.lower()
    assert result.details["http_status"] == 401
    assert result.details["path"] == "/api/system/info"


def test_system_info_diagnoses_404_as_not_a_dhis2_instance() -> None:
    """404 on /api/system/info is unambiguous - this isn't a DHIS2 server."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2SystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "does not look like a DHIS2" in result.message
    assert result.details["http_status"] == 404


def test_modeling_app_diagnoses_403_as_missing_authority() -> None:
    """403 on /api/apps surfaces the required authority so the operator can grant it."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"message": "Forbidden"})

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "403" in result.message
    assert "M_dhis-web-app-management" in result.message
    assert result.details["required_authority"] == "M_dhis-web-app-management"
    assert result.details["http_status"] == 403


def test_modeling_app_generic_500_keeps_status_in_details() -> None:
    """Anything past 401/403/404 still gets a generic line + structured status."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapModelingAppCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "Unexpected status 500" in result.message
    assert result.details["http_status"] == 500


def test_chap_ping_diagnoses_401_with_structured_status() -> None:
    """401 from the chap route surfaces the credential hint + http_status."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapPingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "401" in result.message
    assert "credentials" in result.message.lower()
    assert result.details["http_status"] == 401
    assert result.details["path"] == "/api/routes/chap/run/health"


def test_chap_ping_502_keeps_chapcore_message_and_http_status() -> None:
    """502 keeps its chap-core-specific message but now also lands a structured http_status."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapPingCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "chap-core did not respond" in result.message
    assert result.details["http_status"] == 502


def test_chap_system_info_diagnoses_403_with_structured_status() -> None:
    """403 through the chap route surfaces the authority hint + http_status."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "403" in result.message
    assert result.details["http_status"] == 403


def test_chap_system_info_404_carries_route_specific_hint() -> None:
    """404 keeps the system-info-specific not-found message + structured status."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.FAIL
    assert "404" in result.message
    assert "chap route" in result.message.lower() or "chap-core" in result.message
    assert result.details["http_status"] == 404


# ---------- dhis2_chap_ping body validation ----------


def test_chap_ping_ok_on_chap_core_health_body() -> None:
    """chap-core's /health answers `{"status": "success", "message": "healthy"}`."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"status": "success", "message": "healthy"})

    result = asyncio.run(Dhis2ChapPingCheck().run(_client(handler), _ctx()))
    assert result.status is Status.OK
    assert result.details["health_status"] == "success"
    assert result.details["health_message"] == "healthy"


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/html; charset=utf-8", b"<html><body>Sign in with SSO</body></html>"),
        ("text/plain", b"ok"),
    ],
)
def test_chap_ping_fails_on_non_json_2xx(content_type: str, body: bytes) -> None:
    """A 200 login page from an SSO / reverse proxy must not count as healthy."""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": content_type})

    result = asyncio.run(Dhis2ChapPingCheck().run(_client(handler), _ctx()))
    assert result.status is Status.FAIL
    assert "non-JSON" in result.message
    assert result.details["http_status"] == 200
    assert result.details["path"] == "/api/routes/chap/run/health"


def test_chap_ping_fails_on_malformed_json() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"{not json", headers={"content-type": "application/json"})

    result = asyncio.run(Dhis2ChapPingCheck().run(_client(handler), _ctx()))
    assert result.status is Status.FAIL
    assert "malformed JSON" in result.message


@pytest.mark.parametrize("payload", [["a", "b"], {"something_else": "x"}, {"status": 1}])
def test_chap_ping_fails_on_unexpected_json_shape(payload: object) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    result = asyncio.run(Dhis2ChapPingCheck().run(_client(handler), _ctx()))
    assert result.status is Status.FAIL
    assert "not from chap-core" in result.message


def test_chap_ping_fails_when_chap_core_reports_failure() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"status": "failed", "message": "GEE not configured"})

    result = asyncio.run(Dhis2ChapPingCheck().run(_client(handler), _ctx()))
    assert result.status is Status.FAIL
    assert "failed" in result.message
    assert "GEE not configured" in result.message
    assert result.details["health_status"] == "failed"


# ---------- dhis2_chap_system_info legacy fallback ----------


def test_chap_system_info_falls_back_to_legacy_path_for_old_chap_core() -> None:
    """chap-core 1.1.x - 1.2.0 only expose `/system-info`; a 404 on `/system/info` retries it."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/system-info"):
            return httpx2.Response(200, json={"chap_core_version": "1.2.0", "python_version": "3.12.1"})
        return httpx2.Response(404, content=b"")

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.OK
    assert "chap-core 1.2.0" in result.message
    assert seen == ["/api/routes/chap/run/system/info", "/api/routes/chap/run/system-info"]


def test_chap_system_info_does_not_try_legacy_path_on_success() -> None:
    """A healthy current chap-core is asked once; `auth_required` is surfaced in details."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return httpx2.Response(200, json={"chap_core_version": "2.7.0", "auth_required": True})

    client = _client(handler)
    result = asyncio.run(Dhis2ChapSystemInfoCheck().run(client, _ctx()))
    assert result.status is Status.OK
    assert result.details["auth_required"] is True
    assert seen == ["/api/routes/chap/run/system/info"]
