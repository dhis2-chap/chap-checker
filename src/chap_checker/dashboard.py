"""Textual TUI dashboard for chap-checker.

One tile per configured instance, auto-refreshing on an interval. Shows the
rolled-up status, the cumulative ping success ratio since the dashboard
started, and the latest non-OK message. Whether alerts dispatch is decided at
launch time via ``--alerts`` / ``--no-alerts``; the UI is read-only beyond
the refresh / quit keys.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar

from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid
from textual.widgets import Footer, Header, Static

from chap_checker.checks.base import Status
from chap_checker.config import CheckerConfig
from chap_checker.runner import RunReport, TargetEntry, run_targets

_STATUS_RANK = [Status.ERROR, Status.FAIL, Status.WARN, Status.SKIPPED, Status.OK]

_BORDER_BY_STATUS = {
    Status.OK: "bright_green",
    Status.WARN: "yellow",
    Status.FAIL: "red",
    Status.ERROR: "magenta",
    Status.SKIPPED: "grey42",
}


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
    """Return the worst status in the list (ERROR > FAIL > WARN > SKIPPED > OK)."""
    for s in _STATUS_RANK:
        if s in statuses:
            return s
    return Status.OK


class InstanceTile(Static):
    """One tile per chap-checker target."""

    DEFAULT_CSS = """
    InstanceTile {
        height: 100%;
        width: 100%;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, entry: TargetEntry) -> None:
        super().__init__("", expand=True)
        self.entry = entry
        self.ping_ok = 0
        self.ping_total = 0
        self.last_report: RunReport | None = None
        self._render_initial()

    def _render_initial(self) -> None:
        body = Text()
        body.append(str(self.entry.target.base_url), style="dim")
        body.append("\n\n")
        body.append("(awaiting first refresh)", style="dim italic")
        self.update(Panel(body, title=self.entry.name, border_style="grey42"))

    def update_from(self, report: RunReport) -> None:
        self.last_report = report

        ping = next((r for r in report.results if r.name == "dhis2_ping"), None)
        if ping is not None and ping.status is not Status.SKIPPED:
            self.ping_total += 1
            if ping.status is Status.OK:
                self.ping_ok += 1

        statuses = [r.status for r in report.results]
        ok_n = sum(1 for s in statuses if s is Status.OK)
        total_n = len(statuses)
        worst = _worst(statuses)
        border = _BORDER_BY_STATUS[worst]

        body = Text()
        body.append(str(self.entry.target.base_url), style="dim")
        body.append("\n\n")

        # Big status pill
        if worst is Status.OK:
            body.append(f"OK   {ok_n}/{total_n} checks", style="bold bright_green")
        else:
            body.append(f"{worst.value.upper():4s} {ok_n}/{total_n} checks", style=f"bold {border}")
        body.append("\n")

        if self.ping_total > 0:
            ratio = self.ping_ok / self.ping_total
            pct = math.floor(ratio * 100)
            body.append(
                f"ping: {self.ping_ok}/{self.ping_total} ({pct}%)",
                style="dim" if ratio == 1.0 else "yellow",
            )
            body.append("\n")

        # First non-OK message for context (cap to one line)
        non_ok = [r for r in report.results if r.status is not Status.OK]
        if non_ok:
            first = non_ok[0]
            msg = first.message.split("\n", 1)[0][:80]
            body.append(f"{first.name}: {msg}", style=_BORDER_BY_STATUS.get(first.status, "white"))

        self.update(Panel(body, title=self.entry.name, border_style=border))


class DashboardApp(App[None]):
    """Textual dashboard for chap-checker."""

    CSS: ClassVar[str] = """
    Grid {
        grid-gutter: 1;
        padding: 1 1;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

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
        yield Header(show_clock=True)
        cols = columns_for(len(self.targets))
        rows = math.ceil(len(self.targets) / cols) if self.targets else 1
        grid = Grid(id="tiles")
        grid.styles.grid_size_columns = cols
        grid.styles.grid_size_rows = rows
        with grid:
            for entry in self.targets:
                tile = InstanceTile(entry)
                self.tiles[entry.name] = tile
                yield tile
        yield Footer()

    def on_mount(self) -> None:
        self.title = "chap-checker"
        n = len(self.targets)
        alerts = "ON" if self.alerts_enabled else "OFF"
        self.sub_title = f"{n} instance(s)  ·  alerts {alerts}  ·  refresh every {int(self.interval_s)}s"
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
                # Late import to avoid pulling cli (and its typer surface) at module load.
                from chap_checker.cli import dispatch_alerts_async

                await dispatch_alerts_async(reports, self.targets, self.cfg.alerts, self.state_path)
        finally:
            self._refreshing = False


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


# Silence unused-import noise from the Container import we keep for future
# expansion (e.g. wrapping the grid in a Vertical with a top status bar).
_ = Container
