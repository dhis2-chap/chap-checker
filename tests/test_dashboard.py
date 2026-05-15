"""Unit tests for the dashboard helpers.

The Textual app itself is integration territory (snapshot tests, pilot mode);
here we cover the pure-logic helpers and the tile's update-from-report
behaviour, which don't need a running event loop.
"""

from typing import cast

from pydantic import HttpUrl

from chap_checker.checks.base import CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.dashboard import (
    _THEME_BUILDERS,
    InstanceTile,
    _color_for,
    _extract_dhis2_version,
    _format_relative,
    _textual_theme_name,
    _worst,
    columns_for,
)
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


def test_extract_dhis2_version_happy_path() -> None:
    results = [
        CheckResult(
            name="dhis2_system_info",
            status=Status.OK,
            message="",
            details={"version": "2.42.3"},
            duration_ms=0.0,
        ),
    ]
    assert _extract_dhis2_version(results) == "2.42.3"


def test_extract_dhis2_version_missing_returns_none() -> None:
    results = [
        CheckResult(
            name="dhis2_system_info",
            status=Status.WARN,
            message="no version",
            details={},
            duration_ms=0.0,
        ),
    ]
    assert _extract_dhis2_version(results) is None


def test_format_relative_seconds() -> None:
    from datetime import datetime, timedelta

    now = datetime(2026, 5, 13, 12, 0, 0)
    assert _format_relative(now, now - timedelta(seconds=5)) == "5s ago"
    assert _format_relative(now, now - timedelta(seconds=90)) == "1m ago"
    assert _format_relative(now, now - timedelta(hours=2)) == "2h ago"
    # Future times clamp to 0s.
    assert _format_relative(now, now + timedelta(seconds=5)) == "0s ago"


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


def test_tile_history_appends_worst_status_per_refresh() -> None:
    tile = InstanceTile(_entry())
    # Two clean refreshes, then a WARN, then a FAIL, then a refresh
    # where everything is skipped (which must NOT be recorded).
    for status in (Status.OK, Status.OK, Status.WARN, Status.FAIL):
        tile.update_from(
            RunReport(
                target_name="test",
                target_url="https://test.example",
                results=[
                    CheckResult(name="dhis2_ping", status=status, message="", duration_ms=1.0),
                    CheckResult(name="dhis2_system_info", status=Status.OK, message="", duration_ms=1.0),
                ],
            ),
        )
    tile.update_from(
        RunReport(
            target_name="test",
            target_url="https://test.example",
            results=[
                CheckResult(name="dhis2_ping", status=Status.SKIPPED, message="", duration_ms=0.0),
                CheckResult(name="dhis2_system_info", status=Status.SKIPPED, message="", duration_ms=0.0),
            ],
        ),
    )
    assert list(tile.history) == [Status.OK, Status.OK, Status.WARN, Status.FAIL]


def test_tile_history_caps_at_max_len() -> None:
    tile = InstanceTile(_entry())
    for _ in range(45):
        tile.update_from(
            RunReport(
                target_name="test",
                target_url="https://test.example",
                results=[CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=1.0)],
            ),
        )
    assert len(tile.history) == 30
    assert all(s is Status.OK for s in tile.history)


# ---------- TUI theme mapping ----------


def test_color_for_returns_textual_tokens() -> None:
    """_color_for returns `$`-prefixed Textual markup tokens, not hex.

    Static widgets render this inside Rich markup like `[bold {color}]…[/]`
    and Textual resolves the token against the active theme; hex would
    bypass the theme and lock the colour to one palette.
    """
    assert _color_for(Status.OK) == "$success"
    assert _color_for(Status.WARN) == "$warning"
    assert _color_for(Status.FAIL) == "$error"
    assert _color_for(Status.ERROR) == "$error"
    assert _color_for(Status.SKIPPED) == "$text-disabled"


def test_every_ui_theme_has_a_builder() -> None:
    """Every value in chap_checker.config.UiTheme must have a TUI theme builder.

    Without this, a config like `[ui] theme = \"amber\"` would silently
    fall back to phosphor in the TUI even though the web honours it.
    """
    from typing import get_args

    from chap_checker.config import UiTheme

    for value in get_args(UiTheme):
        assert value in _THEME_BUILDERS, f"missing TUI theme builder for ui.theme={value!r}"


def test_textual_theme_name_resolves_to_registered_theme() -> None:
    """The Textual theme name returned for each ui.theme matches the registered theme."""
    for ui_value, builder in _THEME_BUILDERS.items():
        assert _textual_theme_name(ui_value) == builder().name


def test_textual_theme_name_unknown_falls_back_to_phosphor() -> None:
    """An unknown ui.theme value falls back to phosphor rather than raising."""
    assert _textual_theme_name("nonsense") == _THEME_BUILDERS["phosphor"]().name


def test_dhis2_theme_is_light_mode() -> None:
    """The dhis2 theme must declare dark=False so Textual derives light-mode contrasts."""
    assert _THEME_BUILDERS["dhis2"]().dark is False


def test_all_other_themes_are_dark_mode() -> None:
    """Every non-dhis2 theme stays dark — preserves the original TUI feel."""
    for name, builder in _THEME_BUILDERS.items():
        if name == "dhis2":
            continue
        assert builder().dark is True, f"{name} should be dark=True"
