"""Unit tests for the dashboard helpers and the daemon's tile bookkeeping.

The Textual app itself is integration territory (snapshot tests, pilot mode);
here we cover the pure-logic helpers, the daemon's per-tile counter / history
semantics (refresh_once + TileTracker), and the connect-mode HTTP plumbing.
"""

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import HttpUrl

from chap_checker.checks.base import CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.config import CheckerConfig, InstanceConfig
from chap_checker.daemon import (
    DashboardServer,
    DashboardState,
    TileModel,
    TileTracker,
)
from chap_checker.dashboard import (
    _THEME_BUILDERS,
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


def _server(name: str = "test") -> DashboardServer:
    """Build a DashboardServer with one target, no real config dependencies."""
    cfg = CheckerConfig(
        instances={
            name: InstanceConfig(
                url=cast(HttpUrl, f"https://{name}.example"),
                username="u",
                password="p",
            ),
        },
    )
    return DashboardServer(
        targets=[_entry(name)],
        cfg=cfg,
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )


def _feed_report(server: DashboardServer, name: str, results: list[CheckResult]) -> None:
    """Push a synthetic RunReport into the server's tracker without hitting the network.

    The previous test suite called `InstanceTile.update_from(report)` directly.
    The new state lives on `TileTracker`, so we reproduce the tracker-update
    half of `DashboardServer.refresh_once` here.
    """
    from datetime import datetime

    from chap_checker.daemon import worst

    report = RunReport(
        target_name=name,
        target_url=f"https://{name}.example",
        results=results,
    )
    tracker = server.trackers.setdefault(name, TileTracker())
    tracker.last_report = report
    tracker.last_refresh = datetime.now()
    ping = next((c for c in results if c.name == "dhis2_ping"), None)
    if ping is not None and ping.status is not Status.SKIPPED:
        tracker.ping_total += 1
        if ping.status is Status.OK:
            tracker.ping_ok += 1
    ran: list[Status] = [c.status for c in results if c.status is not Status.SKIPPED]
    if ran:
        durations = [c.duration_ms for c in results if c.status is not Status.SKIPPED]
        avg_latency = int(sum(durations) / len(durations)) if durations else None
        tracker.history.append((worst(ran), avg_latency))


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


def test_tracker_ping_ratio_tracks_successes() -> None:
    server = _server()
    for _ in range(3):
        _feed_report(server, "test", [CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=1.0)])
    _feed_report(server, "test", [CheckResult(name="dhis2_ping", status=Status.FAIL, message="down", duration_ms=1.0)])
    tracker = server.trackers["test"]
    assert tracker.ping_ok == 3
    assert tracker.ping_total == 4


def test_tracker_skipped_ping_does_not_count() -> None:
    server = _server()
    _feed_report(
        server,
        "test",
        [CheckResult(name="dhis2_ping", status=Status.SKIPPED, message="", duration_ms=0.0)],
    )
    assert server.trackers["test"].ping_total == 0


def test_tracker_no_ping_in_results_means_ratio_stays_zero() -> None:
    server = _server()
    _feed_report(
        server,
        "test",
        [CheckResult(name="some_other_check", status=Status.OK, message="", duration_ms=0.0)],
    )
    assert server.trackers["test"].ping_total == 0
    assert server.trackers["test"].ping_ok == 0


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


def test_tracker_records_last_refresh_timestamp() -> None:
    server = _server()
    assert server.trackers["test"].last_refresh is None
    _feed_report(server, "test", [CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=1.0)])
    assert server.trackers["test"].last_refresh is not None


def test_tracker_history_appends_worst_status_per_refresh() -> None:
    server = _server()
    # Two clean refreshes, then a WARN, then a FAIL, then a refresh
    # where everything is skipped (which must NOT be recorded).
    for status in (Status.OK, Status.OK, Status.WARN, Status.FAIL):
        _feed_report(
            server,
            "test",
            [
                CheckResult(name="dhis2_ping", status=status, message="", duration_ms=1.0),
                CheckResult(name="dhis2_system_info", status=Status.OK, message="", duration_ms=1.0),
            ],
        )
    _feed_report(
        server,
        "test",
        [
            CheckResult(name="dhis2_ping", status=Status.SKIPPED, message="", duration_ms=0.0),
            CheckResult(name="dhis2_system_info", status=Status.SKIPPED, message="", duration_ms=0.0),
        ],
    )
    assert [s for s, _ in server.trackers["test"].history] == [Status.OK, Status.OK, Status.WARN, Status.FAIL]


def test_tracker_history_caps_at_max_len() -> None:
    server = _server()
    for _ in range(45):
        _feed_report(
            server,
            "test",
            [CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=1.0)],
        )
    history = server.trackers["test"].history
    assert len(history) == 30
    assert all(s is Status.OK for s, _ in history)


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


@pytest.mark.parametrize("theme", list(_THEME_BUILDERS))
def test_dashboard_app_mounts_under_every_theme(theme: str) -> None:
    """Each ui.theme value must let the DashboardApp mount without a TCSS error.

    Regression for: the first cut used `$text-disabled` as a border colour
    and pill background. That token resolves to ``auto 38%`` (alpha-based
    foreground), which Textual's parser rejects for borders/backgrounds -
    but only at runtime, when the App's theme variables are loaded into
    the stylesheet. Mounting the app under Pilot is the cheapest way to
    catch that class of mistake.
    """
    from chap_checker.config import UiConfig
    from chap_checker.dashboard import DashboardApp

    cfg = CheckerConfig(
        instances={"x": InstanceConfig(url=cast(HttpUrl, "https://x.example"), username="u", password="p")},
        ui=UiConfig(theme=theme),  # type: ignore[arg-type]
    )

    async def _mount() -> None:
        app = DashboardApp(
            targets=[_entry("x")],
            cfg=cfg,
            config_path=None,
            state_path=None,
            interval_s=300.0,
            alerts_enabled=False,
        )
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()

    asyncio.run(_mount())


# ---------- Connect mode (--connect URL) ----------


def _canned_state() -> DashboardState:
    return DashboardState(
        instance_count=1,
        alerts_enabled=False,
        interval_s=30.0,
        ui_title="Remote daemon",
        ui_theme="phosphor",
        tiles=[
            TileModel(
                name="alpha",
                url="https://alpha.example",
                worst_status=Status.OK,
                ok_count=2,
                total_count=2,
                ping_ok=10,
                ping_total=10,
                latency_ms=42,
                uptime_pct=100.0,
            ),
        ],
    )


def test_connect_mode_renders_remote_state() -> None:
    """A successful /api/state fetch mounts tiles from the remote snapshot."""
    from chap_checker.dashboard import DashboardApp

    canned = _canned_state()
    # `raise_for_status` walks the response's request; construct one
    # explicitly so the mocked response behaves like the real thing.
    request = httpx.Request("GET", "http://remote.example:8765/api/state")
    mock_get = AsyncMock(
        return_value=httpx.Response(200, content=canned.model_dump_json().encode(), request=request),
    )

    async def _run() -> None:
        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            app = DashboardApp(
                interval_s=300.0,
                connect_url="http://remote.example:8765",
            )
            async with app.run_test(headless=True) as pilot:
                await pilot.pause()
                await app.action_refresh()
                await pilot.pause()
                assert "alpha" in app.tiles
                assert app.tiles["alpha"].tile_url == "https://alpha.example"

    asyncio.run(_run())


def test_connect_mode_shows_disconnect_banner_on_failure() -> None:
    """A transport error paints the disconnect banner and keeps the app alive."""
    from chap_checker.dashboard import DashboardApp, DisconnectBanner

    async def _run() -> None:
        with patch.object(httpx.AsyncClient, "get", new=AsyncMock(side_effect=httpx.ConnectError("nope"))):
            app = DashboardApp(
                interval_s=300.0,
                connect_url="http://nope.example:1",
            )
            async with app.run_test(headless=True) as pilot:
                await app.action_refresh()
                await pilot.pause()
                banner = app.query_one("#banner", DisconnectBanner)
                assert banner.has_class("visible")
                assert "disconnected from http://nope.example:1" in str(banner.render())

    asyncio.run(_run())


def test_local_mode_requires_targets_and_cfg() -> None:
    """Without connect_url the app must reject construction with no targets / cfg."""
    from chap_checker.dashboard import DashboardApp

    with pytest.raises(ValueError, match="Local mode"):
        DashboardApp(interval_s=300.0)
