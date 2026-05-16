"""Shared check-running daemon backing both the TUI and the web dashboard.

Owns the refresh loop, per-tile counters, sparkline history, and the
JSON projection (`DashboardState`) returned by `web.py`'s `/api/state`
and consumed by `tui.py`'s `--connect` mode. Keeping it in one module
means both surfaces share one refresh cadence, one alert path, and one
view model.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from chap_checker.checks.base import CheckResult, Status
from chap_checker.config import CheckerConfig, load_config
from chap_checker.logging import get_logger
from chap_checker.runner import RunReport, TargetEntry, run_targets

_log = get_logger("daemon")

# Number of past refresh outcomes kept per tile for the uptime strip.
# Matches the web dashboard's `UptimeBars` width so both surfaces tell
# the same story about "the last N checks".
HISTORY_LEN = 30

# Status severity ranking, worst-first. `worst()` walks this in order
# and returns the first status present in its input.
_STATUS_RANK = [Status.ERROR, Status.FAIL, Status.WARN, Status.SKIPPED, Status.OK]

_STATUS_SYMBOL = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.ERROR: "!!",
    Status.SKIPPED: "·",
}


def worst(statuses: list[Status]) -> Status:
    """Return the worst status in the list (or OK for an empty list)."""
    for s in _STATUS_RANK:
        if s in statuses:
            return s
    return Status.OK


def extract_dhis2_version(results: list[CheckResult]) -> str | None:
    """Pull the DHIS2 server version out of the dhis2_system_info check details."""
    for r in results:
        if r.name == "dhis2_system_info":
            v = r.details.get("version")
            if v:
                return str(v)
    return None


class CheckRowModel(BaseModel):
    """Single per-check row in the shared view model."""

    name: str
    status: Status
    symbol: str
    message: str


class HistoryPointModel(BaseModel):
    """One past refresh outcome, used for the uptime sparkline.

    `status` is the worst check status across the refresh - OK if
    everything passed, WARN if any non-fatal check flagged something,
    FAIL/ERROR if any check failed. `latency_ms` is the average
    duration of the non-skipped checks in that refresh.
    """

    status: Status
    latency_ms: int | None = None


class TileModel(BaseModel):
    """One tile's data — the shared view model used by both surfaces."""

    name: str
    url: str
    version: str | None = None
    worst_status: Status
    ok_count: int
    total_count: int
    ping_ok: int
    ping_total: int
    latency_ms: int | None = None
    uptime_pct: float | None = None
    last_refresh: datetime | None = None
    checks: list[CheckRowModel] = []
    history: list[HistoryPointModel] = []


class DashboardState(BaseModel):
    """Snapshot of the whole dashboard.

    Returned over the wire by `/api/state` and consumed directly by
    `tui.py`'s `--connect` mode. The TUI's local mode builds the same
    shape in-process via `DashboardServer.snapshot()`.
    """

    instance_count: int
    alerts_enabled: bool
    interval_s: float
    last_refresh: datetime | None = None
    tiles: list[TileModel] = []
    ui_title: str
    ui_theme: str


class TileTracker(BaseModel):
    """Per-tile cumulative counters that don't survive a server restart.

    Holds the live state for one instance: cumulative ping counters, the
    last `RunReport` for tile rendering, and a rolling history deque
    feeding the uptime strip.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ping_ok: int = 0
    ping_total: int = 0
    last_refresh: datetime | None = None
    last_report: RunReport | None = None
    # Rolling per-refresh history (most recent on the right). Each entry
    # is (worst_status, avg_latency_ms) for one refresh cycle - the bar
    # colour follows the overall check outcome, not ping alone. Kept
    # in-memory only, like the cumulative counters above; the dashboard
    # is a live view, not a metrics store.
    history: deque[tuple[Status, int | None]] = Field(
        default_factory=lambda: deque(maxlen=HISTORY_LEN),
    )


class DashboardServer(BaseModel):
    """Runs the refresh loop and holds per-tile state between cycles.

    Used by both surfaces:

    - `web.py`'s FastAPI app registers `run_loop` as a background task
      and serves `snapshot()` at `GET /api/state`.
    - `dashboard.py`'s local mode also drives `refresh_once()` from
      inside the Textual event loop and reads `snapshot()` each render.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    targets: list[TargetEntry]
    cfg: CheckerConfig
    state_path: Path | None = None
    interval_s: float
    alerts_enabled: bool
    config_path: Path | None = None
    trackers: dict[str, TileTracker] = Field(default_factory=dict)
    last_refresh: datetime | None = None

    def model_post_init(self, _context: object) -> None:
        for entry in self.targets:
            self.trackers.setdefault(entry.name, TileTracker())

    def reload(self) -> tuple[set[str], set[str]]:
        """Re-read `config_path` from disk and swap targets / cfg in place.

        Returns `(added_names, removed_names)` so callers can surface the
        delta to the operator. Trackers for surviving instances are kept
        (their history strip stays warm); trackers for removed instances
        are dropped; trackers for added instances start empty.

        Raises `RuntimeError` when `config_path` is None and the loader's
        own exceptions (`FileNotFoundError`, `tomllib.TOMLDecodeError`,
        `pydantic.ValidationError`) on bad config - callers should catch
        and report rather than let the background loop crash.
        """
        if self.config_path is None:
            raise RuntimeError("No config_path set - server was launched without one.")
        new_cfg = load_config(self.config_path)
        new_targets = [
            entry.to_target_entry(name, default_retry_policy=new_cfg.retry) for name, entry in new_cfg.instances.items()
        ]
        old_names = {t.name for t in self.targets}
        new_names = {t.name for t in new_targets}
        self.targets = new_targets
        self.cfg = new_cfg
        for removed in old_names - new_names:
            self.trackers.pop(removed, None)
        for added in new_names - old_names:
            self.trackers.setdefault(added, TileTracker())
        return new_names - old_names, old_names - new_names

    async def run_loop(self) -> None:
        """Refresh + dispatch every `interval_s` seconds, forever."""
        while True:
            try:
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - background loop must survive
                _log.exception("refresh cycle failed")
            await asyncio.sleep(self.interval_s)

    async def refresh_once(self) -> None:
        """Run one refresh cycle: check every target, update trackers, dispatch alerts."""
        started = datetime.now()
        reports = await run_targets(self.targets, concurrency=self.cfg.concurrency)
        now = datetime.now()
        ok_targets = sum(1 for r in reports if all(c.status is Status.OK for c in r.results))
        _log.info(
            "refresh: %d/%d targets ok in %.0fms",
            ok_targets,
            len(reports),
            (now - started).total_seconds() * 1000,
        )
        for r in reports:
            t = self.trackers.setdefault(r.target_name, TileTracker())
            t.last_report = r
            t.last_refresh = now
            ping = next((c for c in r.results if c.name == "dhis2_ping"), None)
            if ping is not None and ping.status is not Status.SKIPPED:
                t.ping_total += 1
                if ping.status is Status.OK:
                    t.ping_ok += 1
            # Sparkline history reflects the overall worst status of this
            # refresh, not ping alone. Refreshes where every check is
            # SKIPPED don't tell the operator anything new so they're
            # dropped from the history strip.
            run_statuses: list[Status] = [c.status for c in r.results if c.status is not Status.SKIPPED]
            if run_statuses:
                w = worst(run_statuses)
                durations = [c.duration_ms for c in r.results if c.status is not Status.SKIPPED]
                avg_latency = int(sum(durations) / len(durations)) if durations else None
                t.history.append((w, avg_latency))
        self.last_refresh = now

        if self.alerts_enabled and self.cfg.alerts is not None and self.state_path is not None:
            # Late import to keep the cli module loaded only when needed.
            from chap_checker.cli import dispatch_alerts_async

            await dispatch_alerts_async(reports, self.targets, self.cfg.alerts, self.state_path)

    def snapshot(self) -> DashboardState:
        """Build the JSON-serializable snapshot used by both surfaces."""
        return DashboardState(
            instance_count=len(self.targets),
            alerts_enabled=self.alerts_enabled,
            interval_s=self.interval_s,
            last_refresh=self.last_refresh,
            tiles=[self._tile_model(e) for e in self.targets],
            ui_title=self.cfg.ui.title,
            ui_theme=self.cfg.ui.theme,
        )

    def _tile_model(self, entry: TargetEntry) -> TileModel:
        t = self.trackers.get(entry.name) or TileTracker()
        report = t.last_report
        url = str(entry.target.base_url).rstrip("/")
        if report is None:
            return TileModel(
                name=entry.name,
                url=url,
                worst_status=Status.SKIPPED,
                ok_count=0,
                total_count=0,
                ping_ok=t.ping_ok,
                ping_total=t.ping_total,
                history=[HistoryPointModel(status=s, latency_ms=lat) for s, lat in t.history],
            )
        statuses = [r.status for r in report.results]
        w = worst(statuses)
        ok_n = sum(1 for s in statuses if s is Status.OK)
        latency_durations = [r.duration_ms for r in report.results if r.status is not Status.SKIPPED]
        latency_ms: int | None = int(sum(latency_durations) / len(latency_durations)) if latency_durations else None
        uptime_pct: float | None = (100 * t.ping_ok / t.ping_total) if t.ping_total > 0 else None
        return TileModel(
            name=entry.name,
            url=url,
            version=extract_dhis2_version(report.results),
            worst_status=w,
            ok_count=ok_n,
            total_count=len(statuses),
            ping_ok=t.ping_ok,
            ping_total=t.ping_total,
            latency_ms=latency_ms,
            uptime_pct=uptime_pct,
            last_refresh=t.last_refresh,
            checks=[
                CheckRowModel(
                    name=r.name.removeprefix("dhis2_"),
                    status=r.status,
                    symbol=_STATUS_SYMBOL.get(r.status, "?"),
                    message=r.message,
                )
                for r in report.results
            ],
            history=[HistoryPointModel(status=s, latency_ms=lat) for s, lat in t.history],
        )


__all__ = [
    "HISTORY_LEN",
    "CheckRowModel",
    "DashboardServer",
    "DashboardState",
    "HistoryPointModel",
    "TileModel",
    "TileTracker",
    "extract_dhis2_version",
    "worst",
]
