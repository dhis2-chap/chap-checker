import httpx
from dhis2w_client import Dhis2

from chap_checker.checks import all_checks
from chap_checker.checks.base import diagnose_status, format_request_error, parse_dhis2_version


def test_registry_has_builtin_checks() -> None:
    names = {c.name for c in all_checks()}
    assert {
        "dhis2_ping",
        "dhis2_system_info",
        "dhis2_chap_route",
        "dhis2_chap_ping",
        "dhis2_chap_system_info",
        "dhis2_chap_modeling_app",
        "dhis2_chap_climate_app",
    } <= names


def test_check_protocol_fields() -> None:
    for check in all_checks():
        assert isinstance(check.name, str) and check.name
        assert isinstance(check.description, str) and check.description
        assert isinstance(check.order, int)
        assert isinstance(check.requires, list)
        assert all(isinstance(r, str) for r in check.requires)


def test_builtin_requires_reference_known_checks() -> None:
    names = {c.name for c in all_checks()}
    for check in all_checks():
        for req in check.requires:
            assert req in names, f"{check.name} requires unknown check {req!r}"


def test_builtin_checks_run_in_dependency_order() -> None:
    names = [c.name for c in all_checks()]
    expected = [
        "http_2xx",
        "dhis2_ping",
        "dhis2_system_info",
        "dhis2_chap_route",
        "dhis2_chap_ping",
        "dhis2_chap_system_info",
        "dhis2_chap_modeling_app",
        "dhis2_chap_climate_app",
    ]
    assert names == expected


def test_chap_app_checks_require_chap_route() -> None:
    """On a vanilla DHIS2 server the chap apps aren't installed; they should SKIP."""
    by_name = {c.name for c in all_checks()}
    assert by_name  # sanity
    for check in all_checks():
        if check.name in ("dhis2_chap_modeling_app", "dhis2_chap_climate_app"):
            assert "dhis2_chap_route" in check.requires, (
                f"{check.name} should require dhis2_chap_route so plain DHIS2 instances "
                "skip the app probes instead of FAIL-ing on every poll."
            )


def test_format_request_error_carries_type_and_path() -> None:
    """Empty exception messages (e.g. ReadTimeout) still get a useful operator-facing line."""
    msg = format_request_error(httpx.ReadTimeout(""), path="/api/me")
    assert msg.startswith("ReadTimeout (/api/me): ")
    assert msg.endswith("(no message)")


def test_format_request_error_falls_back_without_path() -> None:
    msg = format_request_error(httpx.ConnectError("nodename nor servname provided"))
    assert msg == "ConnectError: nodename nor servname provided"


def test_parse_dhis2_version_maps_supported_minors() -> None:
    """`/api/system/info`-style version strings round-trip to Dhis2 enum members."""
    assert parse_dhis2_version("2.41.0") is Dhis2.V41
    assert parse_dhis2_version("2.42.4-1") is Dhis2.V42
    assert parse_dhis2_version("2.43.0") is Dhis2.V43


def test_parse_dhis2_version_returns_none_for_unsupported_or_garbage() -> None:
    """An out-of-range minor or non-version string returns None, no crash."""
    assert parse_dhis2_version("2.44-SNAPSHOT") is None  # past the generated v43 ceiling
    assert parse_dhis2_version("garbage") is None
    assert parse_dhis2_version("") is None


def test_diagnose_status_returns_none_for_success() -> None:
    """A 2xx-3xx status means the caller should keep going; helper returns None."""
    assert diagnose_status(200, path="/api/anything") is None
    assert diagnose_status(204, path="/api/anything") is None
    assert diagnose_status(301, path="/api/anything") is None


def test_diagnose_status_401_points_at_credentials() -> None:
    msg, details = diagnose_status(401, path="/api/me")  # type: ignore[misc]
    assert "401" in msg and "credentials" in msg.lower()
    assert details == {"http_status": 401, "path": "/api/me"}


def test_diagnose_status_403_with_authority_surfaces_it_in_details() -> None:
    msg, details = diagnose_status(  # type: ignore[misc]
        403, path="/api/apps", required_authority="M_dhis-web-app-management"
    )
    assert "M_dhis-web-app-management" in msg
    assert details["required_authority"] == "M_dhis-web-app-management"
    assert details["http_status"] == 403


def test_diagnose_status_404_uses_caller_supplied_meaning() -> None:
    """The 404 message is endpoint-specific, not generic."""
    msg, details = diagnose_status(  # type: ignore[misc]
        404,
        path="/api/routes",
        not_found_meaning="/api/routes returned 404 - routes were introduced in DHIS2 2.40.",
    )
    assert "2.40" in msg
    assert details["http_status"] == 404


def test_diagnose_status_other_4xx_5xx_carries_status_in_details() -> None:
    msg, details = diagnose_status(503, path="/api/system/info")  # type: ignore[misc]
    assert "503" in msg
    assert details == {"http_status": 503, "path": "/api/system/info"}
