"""Web dashboard for chap-checker.

Same layout and palette as the Textual TUI, rendered in a browser at
``100vh`` so it fills a TV screen with no scroll. A FastAPI app runs the
checks on a background loop and exposes a small JSON state endpoint; the
browser polls it on its own timer and re-renders the tiles client-side.

Architecture:

- Initial GET ``/`` serves a single static HTML page with embedded CSS
  and a tiny JS poller. No build step.
- GET ``/api/state`` returns the current snapshot as JSON.
- A background ``asyncio.create_task`` re-runs ``run_targets`` every
  ``interval_s`` seconds, dispatches alerts (when ``--alerts``), and
  updates the shared state.

The shared state mirrors what the TUI's ``InstanceTile`` tracks so the
two surfaces stay visually consistent.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chap_checker.checks.base import Status
from chap_checker.config import CheckerConfig
from chap_checker.dashboard import _extract_dhis2_version, _worst
from chap_checker.logging import get_logger
from chap_checker.runner import RunReport, TargetEntry, run_targets

_log = get_logger("web")

# Number of past ping results kept per tile for the dashboard's history
# strip. 30 matches the artifact's UptimeBars width — the bars shrink
# proportionally if fewer points are present.
_HISTORY_LEN = 30


_STATUS_SYMBOL = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.ERROR: "!!",
    Status.SKIPPED: "·",
}


class CheckRowModel(BaseModel):
    """Single per-check row sent to the browser."""

    name: str
    status: Status
    symbol: str
    message: str


class HistoryPointModel(BaseModel):
    """One past refresh outcome, used for the uptime sparkline.

    ``status`` is the worst check status across the refresh - OK if
    everything passed, WARN if any non-fatal check flagged something,
    FAIL/ERROR if any check failed. ``latency_ms`` is the average
    duration of the non-skipped checks in that refresh, kept around
    so the bar tooltip can still report a number even though the bar
    color itself is no longer latency-driven.
    """

    status: Status
    latency_ms: int | None = None


class TileModel(BaseModel):
    """One tile's data sent to the browser."""

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
    """Snapshot of the whole dashboard returned by ``/api/state``."""

    instance_count: int
    alerts_enabled: bool
    interval_s: float
    last_refresh: datetime | None = None
    tiles: list[TileModel] = []


@dataclass
class _TileTracker:
    """Per-tile cumulative counters that don't survive a server restart."""

    ping_ok: int = 0
    ping_total: int = 0
    last_refresh: datetime | None = None
    last_report: RunReport | None = None
    # Rolling per-refresh history (most recent on the right). Each entry
    # is (worst_status, avg_latency_ms) for one refresh cycle - the bar
    # colour follows the overall check outcome, not ping alone. Kept
    # in-memory only, like the cumulative counters above; the dashboard
    # is a live view, not a metrics store.
    history: deque[tuple[Status, int | None]] = field(
        default_factory=lambda: deque(maxlen=_HISTORY_LEN),
    )


@dataclass
class DashboardServer:
    """Holds the targets, config, and per-tile counters between refresh cycles."""

    targets: list[TargetEntry]
    cfg: CheckerConfig
    state_path: Path | None
    interval_s: float
    alerts_enabled: bool
    trackers: dict[str, _TileTracker] = field(default_factory=dict)
    last_refresh: datetime | None = None

    def __post_init__(self) -> None:
        for entry in self.targets:
            self.trackers.setdefault(entry.name, _TileTracker())

    async def run_loop(self) -> None:
        """Background task: refresh + dispatch every ``interval_s`` seconds, forever."""
        while True:
            try:
                await self._refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - background loop must survive
                _log.exception("refresh cycle failed")
            await asyncio.sleep(self.interval_s)

    async def _refresh_once(self) -> None:
        reports = await run_targets(self.targets, concurrency=self.cfg.concurrency)
        now = datetime.now()
        for r in reports:
            t = self.trackers.setdefault(r.target_name, _TileTracker())
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
                worst = _worst(run_statuses)
                durations = [c.duration_ms for c in r.results if c.status is not Status.SKIPPED]
                avg_latency = int(sum(durations) / len(durations)) if durations else None
                t.history.append((worst, avg_latency))
        self.last_refresh = now

        if self.alerts_enabled and self.cfg.alerts is not None and self.state_path is not None:
            # Late import to keep the cli module loaded only when needed.
            from chap_checker.cli import dispatch_alerts_async

            await dispatch_alerts_async(reports, self.targets, self.cfg.alerts, self.state_path)

    def snapshot(self) -> DashboardState:
        """Build the JSON-serializable snapshot for ``/api/state``."""
        return DashboardState(
            instance_count=len(self.targets),
            alerts_enabled=self.alerts_enabled,
            interval_s=self.interval_s,
            last_refresh=self.last_refresh,
            tiles=[self._tile_model(e) for e in self.targets],
        )

    def _tile_model(self, entry: TargetEntry) -> TileModel:
        t = self.trackers.get(entry.name) or _TileTracker()
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
        worst = _worst(statuses)
        ok_n = sum(1 for s in statuses if s is Status.OK)
        latency_durations = [r.duration_ms for r in report.results if r.status is not Status.SKIPPED]
        latency_ms: int | None = int(sum(latency_durations) / len(latency_durations)) if latency_durations else None
        uptime_pct: float | None = (100 * t.ping_ok / t.ping_total) if t.ping_total > 0 else None
        return TileModel(
            name=entry.name,
            url=url,
            version=_extract_dhis2_version(report.results),
            worst_status=worst,
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


def make_app(server: DashboardServer) -> FastAPI:
    """Build the FastAPI app, wiring the background refresh task into its lifespan."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        task = asyncio.create_task(server.run_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="chap-checker", lifespan=lifespan)

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(server.snapshot().model_dump(mode="json"))

    # The React SPA + Babel-standalone wiring lives in ``web-ui/`` next to
    # the repo root. StaticFiles serves index.html on GET / and every
    # vendor/src/* asset directly. Path resolved relative to this module
    # so it works from an editable install or a built wheel.
    web_ui = _web_ui_dir()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_ui / "index.html")

    app.mount("/", StaticFiles(directory=str(web_ui), html=False), name="web-ui")

    return app


def _web_ui_dir() -> Path:
    """Return the ``web-ui/`` directory, raising a friendly error if missing."""
    # src/chap_checker/web.py -> ../../web-ui from the repo root.
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent / "web-ui"
    if not (candidate / "index.html").exists():
        raise FileNotFoundError(
            f"web-ui/index.html not found at {candidate}. "
            "The web dashboard expects the React assets to ship alongside the package."
        )
    return candidate


def run(
    targets: list[TargetEntry],
    cfg: CheckerConfig,
    state_path: Path | None,
    interval_s: float = 30.0,
    alerts_enabled: bool = False,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Launch the web dashboard on ``host:port``.

    Use ``host="0.0.0.0"`` to expose on the local network (e.g. for a TV
    in the office). The server has no authentication; either keep it on
    the loopback interface or put it behind a reverse proxy with
    network-level access controls.
    """
    server = DashboardServer(
        targets=targets,
        cfg=cfg,
        state_path=state_path,
        interval_s=interval_s,
        alerts_enabled=alerts_enabled,
    )
    app = make_app(server)
    # Late import - uvicorn is heavyish and only needed when serving.
    import uvicorn

    # Quiet uvicorn's default access log to keep stderr usable.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    uvicorn.run(app, host=host, port=port, log_level="warning")


__all__: list[str] = [
    "CheckRowModel",
    "DashboardServer",
    "DashboardState",
    "HistoryPointModel",
    "TileModel",
    "make_app",
    "run",
]
