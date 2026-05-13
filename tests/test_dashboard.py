"""Unit tests for the dashboard helpers.

The Textual app itself is integration territory (snapshot tests, pilot mode);
here we cover the pure-logic helpers and the tile's update-from-report
behaviour, which don't need a running event loop.
"""

from typing import cast

from pydantic import HttpUrl

from chap_checker.checks.base import CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.dashboard import InstanceTile, _extract_versions, _worst, columns_for
from chap_checker.runner import RunReport, TargetEntry


def _entry(name: str = "test") -> TargetEntry:
    return TargetEntry(
        name=name,
        target=Dhis2Target(
            base_url=cast(HttpUrl, "https://test.example"),
            username="u",
            password="p",
        ),
    )


def test_columns_for_adapts_to_count() -> None:
    assert columns_for(1) == 1
    assert columns_for(2) == 2
    assert columns_for(4) == 2
    assert columns_for(5) == 3
    assert columns_for(9) == 3
    assert columns_for(10) == 4
    assert columns_for(100) == 4


def test_worst_returns_highest_severity_present() -> None:
    assert _worst([Status.OK, Status.WARN, Status.FAIL]) is Status.FAIL
    assert _worst([Status.OK, Status.OK]) is Status.OK
    assert _worst([Status.WARN, Status.OK]) is Status.WARN
    assert _worst([Status.ERROR, Status.FAIL]) is Status.ERROR
    assert _worst([Status.SKIPPED]) is Status.SKIPPED
    assert _worst([]) is Status.OK


def test_tile_ping_ratio_tracks_successes() -> None:
    tile = InstanceTile(_entry())
    # Three OK pings.
    for _ in range(3):
        tile.update_from(
            RunReport(
                target_name="test",
                target_url="https://test.example",
                results=[CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=1.0)],
            )
        )
    # Then one FAIL.
    tile.update_from(
        RunReport(
            target_name="test",
            target_url="https://test.example",
            results=[CheckResult(name="dhis2_ping", status=Status.FAIL, message="down", duration_ms=1.0)],
        )
    )
    assert tile.ping_ok == 3
    assert tile.ping_total == 4


def test_tile_skipped_ping_does_not_count() -> None:
    tile = InstanceTile(_entry())
    tile.update_from(
        RunReport(
            target_name="test",
            target_url="https://test.example",
            results=[
                CheckResult(name="dhis2_ping", status=Status.SKIPPED, message="", duration_ms=0.0),
            ],
        )
    )
    assert tile.ping_total == 0


def test_tile_no_ping_in_results_means_ratio_stays_zero() -> None:
    tile = InstanceTile(_entry())
    tile.update_from(
        RunReport(
            target_name="test",
            target_url="https://test.example",
            results=[CheckResult(name="some_other_check", status=Status.OK, message="", duration_ms=0.0)],
        )
    )
    assert tile.ping_total == 0
    assert tile.ping_ok == 0


def test_extract_versions_pulls_known_checks() -> None:
    results = [
        CheckResult(
            name="dhis2_system_info",
            status=Status.OK,
            message="",
            details={"version": "2.42.3"},
            duration_ms=0.0,
        ),
        CheckResult(
            name="dhis2_chap_system_info",
            status=Status.OK,
            message="",
            details={"chap_core_version": "1.4.0", "python_version": "3.13.0"},
            duration_ms=0.0,
        ),
        CheckResult(
            name="dhis2_chap_modeling_app",
            status=Status.OK,
            message="",
            details={"version": "5.0.0"},
            duration_ms=0.0,
        ),
    ]
    versions = _extract_versions(results)
    assert ("DHIS2", "2.42.3") in versions
    assert ("chap-core", "1.4.0") in versions
    assert ("Modeling", "5.0.0") in versions


def test_extract_versions_skips_when_field_missing() -> None:
    results = [
        CheckResult(
            name="dhis2_system_info",
            status=Status.WARN,
            message="no version",
            details={},
            duration_ms=0.0,
        ),
    ]
    assert _extract_versions(results) == []


def test_tile_records_last_refresh_timestamp() -> None:
    tile = InstanceTile(_entry())
    assert tile.last_refresh is None
    tile.update_from(
        RunReport(
            target_name="test",
            target_url="https://test.example",
            results=[CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=1.0)],
        )
    )
    assert tile.last_refresh is not None
