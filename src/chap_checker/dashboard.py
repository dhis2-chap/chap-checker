"""Textual TUI dashboard for chap-checker.

One tile per configured instance. Designed to be left on a TV / monitor so
the operator can see at a glance which targets are up, which version they're
running, and which check just failed. Whether alerts dispatch is decided at
launch time via ``--alerts`` / ``--no-alerts``.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Grid, Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

from chap_checker.checks.base import CheckResult, Status
from chap_checker.config import CheckerConfig, load_config
from chap_checker.runner import RunReport, TargetEntry, run_targets

_ACCENT = "#7DD345"

_STATUS_RANK = [Status.ERROR, Status.FAIL, Status.WARN, Status.SKIPPED, Status.OK]

_PILL_CLASS_BY_STATUS = {
    Status.OK: "pill-ok",
    Status.WARN: "pill-warn",
    Status.FAIL: "pill-fail",
    Status.ERROR: "pill-error",
    Status.SKIPPED: "pill-skipped",
}

_SYMBOL_BY_STATUS = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.ERROR: "!!",
    Status.SKIPPED: "·",
}

# Number of past-refresh outcomes kept per tile for the uptime strip.
# Matches the web dashboard's `UptimeBars` width so both surfaces tell
# the same story about "the last N checks".
_HISTORY_LEN = 30

# Per-status colour used by the uptime strip. OK / WARN / FAIL share
# colours with the existing pill palette so the strip stays consistent
# with the rest of the tile.
_STRIP_COLOR_BY_STATUS = {
    Status.OK: "#7DD345",
    Status.WARN: "#d4a017",
    Status.FAIL: "#d04040",
    Status.ERROR: "#c050c0",
    Status.SKIPPED: "#444",
}
# Dim slot rendered before history has filled up - mirrors the web
# dashboard's `{noData: true}` padding behaviour. Lifted slightly
# above the tile background (#161616) so the placeholder cells stay
# visible instead of melting into the surrounding panel.
_STRIP_COLOR_EMPTY = "#3a3a3a"


class UptimeStrip(Widget):
    """One-row coloured-block history strip that fills its widget width.

    Each cell is one terminal column wide; the rightmost cell is the
    most recent refresh and the strip pads with dim placeholders on
    the left until enough history accumulates. Reads ``self.size``
    at render time so the strip spans the full tile width regardless
    of how many instances are configured.
    """

    DEFAULT_CSS = """
    UptimeStrip {
        height: 1;
        width: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, history: deque[Status]) -> None:
        super().__init__()
        self._history = history

    def render(self) -> Text:
        # Padding eats one cell on each side (`padding: 0 1`).
        width = max(1, self.size.width - 2)
        slots = list(self._history)
        text = Text()
        if width <= len(slots):
            # Tile narrower than the buffer - show the most recent
            # `width` refreshes, one cell each.
            for status in slots[-width:]:
                text.append("█", style=_STRIP_COLOR_BY_STATUS.get(status, _STRIP_COLOR_EMPTY))
            return text
        # Tile wider than (or equal to) the buffer - dim padding on
        # the left, real history on the right.
        text.append("█" * (width - len(slots)), style=_STRIP_COLOR_EMPTY)
        for status in slots:
            text.append("█", style=_STRIP_COLOR_BY_STATUS.get(status, _STRIP_COLOR_EMPTY))
        return text


def columns_for(n_instances: int) -> int:
    """Pick a column count that looks balanced for ``n_instances`` tiles."""
    if n_instances <= 1:
        return 1
    if n_instances <= 4:
        return 2
    if n_instances <= 9:
        return 3
    return 4


def _worst(statuses: list[Status]) -> Status:
    """Return the worst status in the list."""
    for s in _STATUS_RANK:
        if s in statuses:
            return s
    return Status.OK


def _extract_dhis2_version(results: list[CheckResult]) -> str | None:
    """Pull the DHIS2 server version out of the dhis2_system_info check details."""
    for r in results:
        if r.name == "dhis2_system_info":
            v = r.details.get("version")
            if v:
                return str(v)
    return None


def _format_relative(now: datetime, then: datetime) -> str:
    """Render ``then`` as 'Ns ago' / 'Nm ago' / 'Nh ago'."""
    delta = max(0, int((now - then).total_seconds()))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


class DashboardHeader(Horizontal):
    """Custom top bar: brand | N instance(s) | alerts ... | refresh every Ns ... HH:MM:SS."""

    DEFAULT_CSS = """
    DashboardHeader {
        height: 1;
        padding: 0 2;
        background: $background;
    }
    DashboardHeader .hdr-name {
        color: #7DD345;
        text-style: bold;
        width: auto;
    }
    DashboardHeader .hdr-pipe {
        color: #555;
        width: 3;
        content-align: center middle;
    }
    DashboardHeader .hdr-text {
        color: #aaa;
        width: auto;
    }
    DashboardHeader .hdr-clock {
        color: #aaa;
        width: 1fr;
        content-align: right middle;
    }
    """

    def __init__(
        self,
        n_instances: int,
        alerts_enabled: bool,
        interval_s: float,
        title: str,
    ) -> None:
        super().__init__()
        self._n = n_instances
        self._alerts = alerts_enabled
        self._interval = interval_s
        self._title = title

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="hdr-name", id="hdr-name")
        yield Static("|", classes="hdr-pipe")
        yield Static(f"{self._n} instance(s)", classes="hdr-text")
        yield Static("|", classes="hdr-pipe")
        yield Static(f"alerts {'ON' if self._alerts else 'OFF'}", classes="hdr-text")
        yield Static("|", classes="hdr-pipe")
        yield Static(f"refresh every {int(self._interval)}s", classes="hdr-text")
        yield Static("--:--:--", classes="hdr-clock", id="hdr-clock")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick_clock)
        self._tick_clock()

    def _tick_clock(self) -> None:
        self.query_one("#hdr-clock", Static).update(datetime.now().strftime("%H:%M:%S"))


class DashboardFooter(Horizontal):
    """Custom bottom bar showing the key bindings."""

    DEFAULT_CSS = """
    DashboardFooter {
        height: 1;
        padding: 0 2;
        background: $background;
    }
    DashboardFooter Static {
        width: auto;
        color: #aaa;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(f"[bold {_ACCENT}]\\[q][/] quit   [bold {_ACCENT}]\\[r][/] refresh")


class CheckRow(Horizontal):
    """Single row in a tile's 'CHECKS' section: name on the left, status symbol on the right."""

    DEFAULT_CSS = """
    CheckRow {
        height: 1;
    }
    CheckRow .check-name {
        width: 1fr;
        color: #bbb;
    }
    CheckRow .check-status {
        width: auto;
        content-align: right middle;
    }
    """

    def __init__(self, check_name: str, status: Status) -> None:
        super().__init__()
        self.check_name = check_name
        self.check_status = status

    def compose(self) -> ComposeResult:
        # Strip the leading `dhis2_` namespace; keep `chap_` so chap-specific
        # checks remain distinguishable from their DHIS2 counterparts in the
        # condensed display (e.g. dhis2_ping -> ping, dhis2_chap_ping -> chap_ping).
        short = self.check_name.removeprefix("dhis2_")
        symbol = _SYMBOL_BY_STATUS.get(self.check_status, "?")
        color = _ACCENT if self.check_status is Status.OK else _color_for(self.check_status)
        yield Static(short, classes="check-name")
        yield Static(f"[{color}]{symbol}[/]", classes="check-status")


def _color_for(status: Status) -> str:
    return {
        Status.OK: _ACCENT,
        Status.WARN: "yellow",
        Status.FAIL: "#d04040",
        Status.ERROR: "#c050c0",
        Status.SKIPPED: "#666",
    }.get(status, "white")


class InstanceTile(Container):
    """One tile per chap-checker target."""

    DEFAULT_CSS = """
    InstanceTile {
        background: #161616;
        border-left: thick #555;
        padding: 1 2;
        height: 100%;
        layout: vertical;
    }
    InstanceTile.tile-status-ok {
        border-left: thick #7DD345;
    }
    InstanceTile.tile-status-warn {
        border-left: thick #d4a017;
        background: #1a1810;
    }
    InstanceTile.tile-status-fail {
        border-left: thick #d04040;
        background: #1d1212;
    }
    InstanceTile.tile-status-error {
        border-left: thick #c050c0;
        background: #1d1218;
    }
    InstanceTile.tile-status-skipped {
        border-left: thick #555;
    }
    InstanceTile .row {
        height: 1;
    }
    InstanceTile .tile-name {
        color: #7DD345;
        text-style: bold;
        width: 1fr;
    }
    InstanceTile .tile-version {
        color: #aaa;
        width: auto;
        content-align: right middle;
    }
    InstanceTile .tile-url {
        color: #888;
        height: 1;
        margin-bottom: 1;
    }
    InstanceTile .pill {
        width: auto;
        padding: 0 1;
        margin-right: 2;
    }
    InstanceTile .pill-ok {
        background: #2da44e;
        color: black;
        text-style: bold;
    }
    InstanceTile .pill-warn {
        background: #d4a017;
        color: black;
        text-style: bold;
    }
    InstanceTile .pill-fail {
        background: #d04040;
        color: white;
        text-style: bold;
    }
    InstanceTile .pill-error {
        background: #c050c0;
        color: white;
        text-style: bold;
    }
    InstanceTile .pill-skipped {
        background: #555;
        color: #ccc;
    }
    InstanceTile .summary {
        width: auto;
        color: #ddd;
        text-style: bold;
        margin-right: 3;
    }
    InstanceTile .ping {
        width: 1fr;
        color: #888;
    }
    InstanceTile .checks-header {
        color: #555;
        text-style: bold;
        height: 1;
        padding-top: 1;
        padding-bottom: 0;
    }
    InstanceTile #checks {
        height: auto;
    }
    InstanceTile .tile-footer {
        dock: bottom;
        height: auto;
        layout: vertical;
    }
    InstanceTile .uptime-header {
        height: 1;
        color: #555;
        padding: 0 1;
    }
    InstanceTile UptimeStrip {
        margin-bottom: 1;
    }
    InstanceTile .stats-row {
        height: 3;
        padding-top: 1;
        align: center top;
    }
    InstanceTile .stat-cell {
        width: 1fr;
        height: 3;
    }
    InstanceTile .stat-label {
        color: #555;
        content-align: center top;
        height: 1;
    }
    InstanceTile .stat-value {
        color: #ddd;
        text-style: bold;
        content-align: center top;
        height: 1;
    }
    """

    def __init__(self, entry: TargetEntry) -> None:
        super().__init__()
        self.entry = entry
        self.ping_ok = 0
        self.ping_total = 0
        self.last_report: RunReport | None = None
        self.last_refresh: datetime | None = None
        # Rolling per-refresh worst-status history for the uptime strip.
        # Refreshes where every check was SKIPPED are dropped so the
        # strip stays meaningful while upstream services flap.
        self.history: deque[Status] = deque(maxlen=_HISTORY_LEN)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="row"):
            yield Static(self.entry.name.upper(), classes="tile-name")
            yield Static("", classes="tile-version", id="version")
        yield Static(f"⊕ {str(self.entry.target.base_url).rstrip('/')}", classes="tile-url")
        with Horizontal(classes="row"):
            yield Static("", classes="pill pill-skipped", id="pill")
            yield Static("", classes="summary", id="summary")
            yield Static("", classes="ping", id="ping")
        yield Static("CHECKS", classes="checks-header")
        yield Vertical(id="checks")
        # Footer block: uptime header + history strip + stats row all
        # dock to the bottom together. Individually docking each piece
        # didn't compose well with the existing stats-row dock and the
        # strip ended up collapsed to zero height.
        with Vertical(classes="tile-footer"):
            yield Static(
                f"UPTIME · LAST {_HISTORY_LEN} CHECKS",
                classes="uptime-header",
                id="uptime-header",
            )
            yield UptimeStrip(self.history)
            with Horizontal(classes="stats-row"):
                with Vertical(classes="stat-cell"):
                    yield Static("latency", classes="stat-label")
                    yield Static("--", classes="stat-value", id="latency")
                with Vertical(classes="stat-cell"):
                    yield Static("updated", classes="stat-label")
                    yield Static("--", classes="stat-value", id="updated")
                with Vertical(classes="stat-cell"):
                    yield Static("uptime", classes="stat-label")
                    yield Static("--", classes="stat-value", id="uptime")

    def on_mount(self) -> None:
        # Tick the "updated Xs ago" string every second.
        self.set_interval(1.0, self._tick_updated)

    def update_from(self, report: RunReport) -> None:
        # Data updates (always).
        self.last_report = report
        self.last_refresh = datetime.now()
        ping = next((r for r in report.results if r.name == "dhis2_ping"), None)
        if ping is not None and ping.status is not Status.SKIPPED:
            self.ping_total += 1
            if ping.status is Status.OK:
                self.ping_ok += 1
        # Append the refresh's worst non-skipped status to history. The
        # strip reflects "how the last 30 refreshes went overall", not
        # just whether ping reached the server.
        ran_statuses: list[Status] = [r.status for r in report.results if r.status is not Status.SKIPPED]
        if ran_statuses:
            self.history.append(_worst(ran_statuses))

        # UI updates only when the widget is actually mounted in an app.
        # Unit tests construct tiles outside an app and just inspect data fields.
        if not self.is_mounted:
            return
        self._render_tile(report)

    def _render_tile(self, report: RunReport) -> None:
        # Title-row version
        v = _extract_dhis2_version(report.results)
        self.query_one("#version", Static).update(f"DHIS2  {v}" if v else "")

        # Status pill + whole-tile status class (drives the accent border
        # and a faint background tint so a FAIL tile is unmistakable).
        statuses = [r.status for r in report.results]
        worst = _worst(statuses)
        for s in Status:
            self.set_class(s is worst, f"tile-status-{s.value}")
        pill = self.query_one("#pill", Static)
        pill.update(worst.value.upper())
        pill.set_classes(f"pill {_PILL_CLASS_BY_STATUS[worst]}")

        # Summary + ping
        ok_n = sum(1 for s in statuses if s is Status.OK)
        total_n = len(statuses)
        self.query_one("#summary", Static).update(f"{ok_n}/{total_n} checks")
        if self.ping_total > 0:
            pct = math.floor(100 * self.ping_ok / self.ping_total)
            self.query_one("#ping", Static).update(f"{self.ping_ok}/{self.ping_total} ping ({pct}%)")
        else:
            self.query_one("#ping", Static).update("")

        # Per-check rows: clear and rebuild.
        checks = self.query_one("#checks", Vertical)
        checks.remove_children()
        for r in report.results:
            checks.mount(CheckRow(r.name, r.status))

        # Latency: average across all checks that actually ran.
        durations = [r.duration_ms for r in report.results if r.status is not Status.SKIPPED]
        if durations:
            avg = sum(durations) / len(durations)
            self.query_one("#latency", Static).update(f"{int(avg)}ms")
        else:
            self.query_one("#latency", Static).update("--")

        # Uptime: cumulative ping ratio as a percentage.
        if self.ping_total > 0:
            pct_f = 100 * self.ping_ok / self.ping_total
            self.query_one("#uptime", Static).update(f"{pct_f:.2f}%")
        else:
            self.query_one("#uptime", Static).update("--")

        # Uptime header + strip. The strip is a custom widget that
        # reads its width at render time so it fills the tile rather
        # than rendering a fixed 30-cell run; just nudge it to repaint.
        self.query_one("#uptime-header", Static).update(self._render_history_header())
        self.query_one(UptimeStrip).refresh()

        self._tick_updated()

    def _render_history_header(self) -> str:
        """Render the 'UPTIME · LAST 30 CHECKS    100%' header line."""
        label = f"UPTIME · LAST {_HISTORY_LEN} CHECKS"
        if self.history:
            clean = sum(1 for s in self.history if s is Status.OK)
            pct = int(round(100 * clean / len(self.history)))
            return f"{label}  [#888]{pct}%[/]"
        return label

    def _tick_updated(self) -> None:
        if self.last_refresh is None:
            return
        text = _format_relative(datetime.now(), self.last_refresh)
        # The widget may not be mounted yet on the first tick before compose() finishes.
        try:
            self.query_one("#updated", Static).update(text)
        except Exception:  # noqa: BLE001
            pass


class ChapCheckerCommands(Provider):
    """Custom command-palette entries for the chap-checker dashboard.

    Surfaces in Textual's built-in palette (Ctrl+P). Mirrors the web
    dashboard's palette so the same items are available in both surfaces.
    """

    def _entries(self) -> list[tuple[str, str, Callable[[], Any]]]:
        app = self.app
        return [
            (
                "Refresh now",
                "Re-run every check immediately.",
                partial(app.run_action, "refresh"),
            ),
            (
                "Reload config",
                "Re-read chap-checker.toml from disk.",
                partial(app.run_action, "reload"),
            ),
            (
                "Open GitHub repository",
                "github.com/dhis2-chap/chap-checker",
                partial(app.open_url, "https://github.com/dhis2-chap/chap-checker"),
            ),
            (
                "Open documentation",
                "dhis2-chap.github.io/chap-checker",
                partial(app.open_url, "https://dhis2-chap.github.io/chap-checker/"),
            ),
        ]

    async def search(self, query: str) -> Hits:
        """Yield palette hits matching ``query`` (filtered + scored)."""
        matcher = self.matcher(query)
        for name, help_text, callback in self._entries():
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    callback,
                    help=help_text,
                )

    async def discover(self) -> Hits:
        """Yield every entry without filtering (shown when the palette opens empty)."""
        for name, help_text, callback in self._entries():
            yield DiscoveryHit(name, callback, help=help_text)


class DashboardApp(App[None]):
    """Textual dashboard for chap-checker."""

    CSS = """
    Screen {
        background: #0e0e0e;
    }
    #grid {
        grid-gutter: 1 1;
        padding: 1;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+r", "reload", "Reload config"),
    ]

    COMMANDS = App.COMMANDS | {ChapCheckerCommands}

    def __init__(
        self,
        targets: list[TargetEntry],
        cfg: CheckerConfig,
        config_path: Path | None,
        state_path: Path | None,
        interval_s: float = 30.0,
        alerts_enabled: bool = False,
    ) -> None:
        super().__init__()
        self.targets = targets
        self.cfg = cfg
        self.config_path = config_path
        self.state_path = state_path
        self.interval_s = interval_s
        self.alerts_enabled = alerts_enabled
        self.tiles: dict[str, InstanceTile] = {}
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield DashboardHeader(
            n_instances=len(self.targets),
            alerts_enabled=self.alerts_enabled,
            interval_s=self.interval_s,
            title=self.cfg.ui.title,
        )
        cols = columns_for(len(self.targets))
        rows = math.ceil(len(self.targets) / cols) if self.targets else 1
        grid = Grid(id="grid")
        grid.styles.grid_size_columns = cols
        grid.styles.grid_size_rows = rows
        with grid:
            for entry in self.targets:
                tile = InstanceTile(entry)
                self.tiles[entry.name] = tile
                yield tile
        yield DashboardFooter()

    def on_mount(self) -> None:
        self.set_interval(self.interval_s, self.action_refresh)
        self.call_after_refresh(self.action_refresh)

    async def action_refresh(self) -> None:
        """Run checks against all targets, update tiles, optionally dispatch alerts."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            reports = await run_targets(self.targets, concurrency=self.cfg.concurrency)
            for r in reports:
                tile = self.tiles.get(r.target_name)
                if tile is not None:
                    tile.update_from(r)
            if self.alerts_enabled and self.cfg.alerts is not None and self.state_path is not None:
                from chap_checker.cli import dispatch_alerts_async

                await dispatch_alerts_async(reports, self.targets, self.cfg.alerts, self.state_path)
        finally:
            self._refreshing = False

    async def action_reload(self) -> None:
        """Re-read ``chap-checker.toml`` and apply the new config in place.

        Targets and per-target check sets / auth / urls are swapped so the
        next refresh uses the new config. Tiles are not rebuilt — if the
        instance set changed, a notification points the operator at a
        restart so the grid reflects the new layout.
        """
        if self.config_path is None:
            self.notify("No config path — running with ad-hoc target.", severity="warning")
            return
        try:
            new_cfg = load_config(self.config_path)
        except Exception as exc:  # noqa: BLE001 - any error surfaces as a toast
            self.notify(f"Reload failed: {exc}", severity="error")
            return
        new_targets = [entry.to_target_entry(name) for name, entry in new_cfg.instances.items()]
        old_names = {t.name for t in self.targets}
        new_names = {t.name for t in new_targets}
        old_title = self.cfg.ui.title
        self.targets = new_targets
        self.cfg = new_cfg
        if new_cfg.ui.title != old_title:
            try:
                self.query_one("#hdr-name", Static).update(new_cfg.ui.title)
            except Exception:  # noqa: BLE001 - header may not be mounted yet
                pass
        if old_names != new_names:
            added = sorted(new_names - old_names)
            removed = sorted(old_names - new_names)
            parts: list[str] = []
            if added:
                parts.append(f"+{len(added)} ({', '.join(added)})")
            if removed:
                parts.append(f"-{len(removed)} ({', '.join(removed)})")
            self.notify(
                f"Instance set changed: {' '.join(parts)}. Restart dashboard to update tiles.",
                severity="warning",
            )
        else:
            self.notify(f"Reloaded {self.config_path.name} ({len(self.targets)} instance(s)).")


def run(
    targets: list[TargetEntry],
    cfg: CheckerConfig,
    config_path: Path | None,
    state_path: Path | None,
    interval_s: float = 30.0,
    alerts_enabled: bool = False,
) -> None:
    """Launch the TUI dashboard."""
    DashboardApp(
        targets=targets,
        cfg=cfg,
        config_path=config_path,
        state_path=state_path,
        interval_s=interval_s,
        alerts_enabled=alerts_enabled,
    ).run()


# Keep these accessible to tests / future widgets.
__all__ = [
    "CheckRow",
    "DashboardApp",
    "DashboardFooter",
    "DashboardHeader",
    "InstanceTile",
    "UptimeStrip",
    "Widget",
    "columns_for",
    "run",
]
