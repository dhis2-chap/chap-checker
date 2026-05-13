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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from chap_checker.checks.base import Status
from chap_checker.config import CheckerConfig
from chap_checker.dashboard import _extract_dhis2_version, _worst
from chap_checker.logging import get_logger
from chap_checker.runner import RunReport, TargetEntry, run_targets

_log = get_logger("web")


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

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        # Manual JSONResponse so we can sort_keys + a stable indent for debugging.
        return JSONResponse(server.snapshot().model_dump(mode="json"))

    return app


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
    "TileModel",
    "make_app",
    "run",
]


# ---------------------------------------------------------------------------
# Static HTML page. Embedded as a string so there's no template / static-file
# wiring to ship. Hand-written to match the TUI's palette and tile layout.
# ---------------------------------------------------------------------------

_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>chap-checker</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #0e0e0e;
      --tile-bg: #161616;
      --tile-bg-warn: #1a1810;
      --tile-bg-fail: #1d1212;
      --tile-bg-error: #1d1218;
      --accent: #7DD345;
      --text: #ddd;
      --dim: #888;
      --muted: #555;
      --warn: #d4a017;
      --fail: #d04040;
      --error: #c050c0;
      --skipped: #555;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 0;
      height: 100vh;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: 'JetBrains Mono', 'Fira Code', Menlo, Consolas, monospace;
      font-size: 14px;
    }
    body {
      display: flex;
      flex-direction: column;
    }
    header.topbar {
      height: 28px;
      flex: 0 0 28px;
      padding: 0 12px;
      display: flex;
      align-items: center;
      gap: 12px;
      color: var(--dim);
      font-size: 13px;
    }
    header.topbar .name { color: var(--accent); font-weight: 700; }
    header.topbar .pipe { color: var(--muted); }
    header.topbar .clock { margin-left: auto; }

    #grid {
      flex: 1 1 auto;
      display: grid;
      gap: 8px;
      padding: 8px;
      min-height: 0;
    }

    .tile {
      background: var(--tile-bg);
      border-left: 4px solid var(--muted);
      padding: 10px 14px;
      display: flex;
      flex-direction: column;
      min-height: 0;
      overflow: hidden;
    }
    .tile.status-ok { border-left-color: var(--accent); }
    .tile.status-warn { border-left-color: var(--warn); background: var(--tile-bg-warn); }
    .tile.status-fail { border-left-color: var(--fail); background: var(--tile-bg-fail); }
    .tile.status-error { border-left-color: var(--error); background: var(--tile-bg-error); }
    .tile.status-skipped { border-left-color: var(--skipped); }

    .tile-title {
      display: flex;
      align-items: baseline;
      gap: 12px;
    }
    .tile-name {
      color: var(--accent);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      flex: 1 1 auto;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .tile-version {
      color: var(--dim);
      font-size: 13px;
      white-space: nowrap;
    }
    .tile-url {
      color: var(--dim);
      font-size: 12px;
      margin-bottom: 10px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .pillrow {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 6px;
    }
    .pill {
      display: inline-block;
      padding: 1px 8px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }
    .pill-ok { background: #2da44e; color: #000; }
    .pill-warn { background: var(--warn); color: #000; }
    .pill-fail { background: var(--fail); color: #fff; }
    .pill-error { background: var(--error); color: #fff; }
    .pill-skipped { background: var(--skipped); color: #ccc; }
    .summary { color: var(--text); font-weight: 700; }
    .ping { color: var(--dim); font-size: 12px; }

    .checks-header {
      color: var(--muted);
      font-weight: 700;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-top: 8px;
      margin-bottom: 4px;
    }
    .checks {
      display: flex;
      flex-direction: column;
      gap: 2px;
      overflow: hidden;
    }
    .check-row {
      display: flex;
      justify-content: space-between;
      color: #bbb;
      font-size: 13px;
      overflow: hidden;
    }
    .check-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .check-symbol { font-weight: 700; }
    .check-symbol-ok { color: var(--accent); }
    .check-symbol-warn { color: var(--warn); }
    .check-symbol-fail { color: var(--fail); }
    .check-symbol-error { color: var(--error); }
    .check-symbol-skipped { color: var(--skipped); }

    .stats {
      margin-top: auto;
      padding-top: 10px;
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      text-align: center;
    }
    .stat-label { color: var(--muted); font-size: 11px; text-transform: lowercase; }
    .stat-value { color: var(--text); font-weight: 700; font-size: 14px; }

    footer.statusbar {
      height: 22px;
      flex: 0 0 22px;
      padding: 0 12px;
      color: var(--dim);
      font-size: 12px;
      display: flex;
      align-items: center;
      gap: 16px;
    }
    footer.statusbar .ok { color: var(--accent); }
    footer.statusbar .stale { color: var(--warn); }
    footer.statusbar .keys { margin-left: auto; color: var(--muted); }
    footer.statusbar .keys b { color: var(--accent); font-weight: 700; }

    /* Command palette overlay */
    #palette-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.55);
      display: none;
      align-items: flex-start;
      justify-content: center;
      padding-top: 12vh;
      z-index: 100;
    }
    #palette-backdrop.open { display: flex; }
    #palette {
      background: #1a1a1a;
      border: 1px solid #333;
      width: min(560px, 90vw);
      padding: 8px;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.7);
    }
    #palette-input {
      width: 100%;
      background: #0e0e0e;
      color: var(--text);
      border: 1px solid #333;
      padding: 10px 12px;
      font-family: inherit;
      font-size: 14px;
      outline: none;
      box-sizing: border-box;
    }
    #palette-input:focus { border-color: var(--accent); }
    #palette-list {
      list-style: none;
      margin: 8px 0 0;
      padding: 0;
      max-height: 50vh;
      overflow-y: auto;
    }
    .palette-item {
      display: flex;
      align-items: center;
      padding: 8px 12px;
      color: var(--text);
      cursor: pointer;
      font-size: 14px;
    }
    .palette-item.active { background: #222; border-left: 3px solid var(--accent); padding-left: 9px; }
    .palette-item .label { flex: 1 1 auto; }
    .palette-item .hint { color: var(--muted); font-size: 12px; margin-left: 12px; }
  </style>
</head>
<body>
  <header class="topbar">
    <span class="name">chap-checker</span>
    <span class="pipe">|</span>
    <span id="hdr-count">- instance(s)</span>
    <span class="pipe">|</span>
    <span id="hdr-alerts">alerts ...</span>
    <span class="pipe">|</span>
    <span id="hdr-interval">refresh every -s</span>
    <span class="clock" id="clock">--:--:--</span>
  </header>

  <div id="grid"></div>

  <footer class="statusbar">
    <span id="last-refresh">awaiting first refresh ...</span>
    <span class="keys">
      <b>r</b> refresh  ·  <b>⌘K</b> / <b>^K</b> palette
    </span>
  </footer>

  <div id="palette-backdrop" role="dialog" aria-modal="true" aria-label="Command palette">
    <div id="palette">
      <input id="palette-input" type="text" placeholder="Type a command..." autocomplete="off">
      <ul id="palette-list"></ul>
    </div>
  </div>

  <script>
    const STATUS_CLASSES = {
      ok: "status-ok",
      warn: "status-warn",
      fail: "status-fail",
      error: "status-error",
      skipped: "status-skipped",
    };
    const PILL_CLASSES = {
      ok: "pill pill-ok",
      warn: "pill pill-warn",
      fail: "pill pill-fail",
      error: "pill pill-error",
      skipped: "pill pill-skipped",
    };

    // Pick a column count that mirrors the TUI's adaptive grid.
    function columnsFor(n) {
      if (n <= 1) return 1;
      if (n <= 4) return 2;
      if (n <= 9) return 3;
      return 4;
    }

    function fmtRelative(now, then) {
      if (!then) return "-";
      const delta = Math.max(0, Math.floor((now - then) / 1000));
      if (delta < 60) return delta + "s ago";
      if (delta < 3600) return Math.floor(delta / 60) + "m ago";
      return Math.floor(delta / 3600) + "h ago";
    }

    function tileHtml(t, now) {
      const cls = STATUS_CLASSES[t.worst_status] || "status-skipped";
      const pillCls = PILL_CLASSES[t.worst_status] || "pill pill-skipped";
      let pingLine = "";
      if (t.ping_total > 0) {
        const pct = Math.floor(100 * t.ping_ok / t.ping_total);
        pingLine = `<span class="ping">${t.ping_ok}/${t.ping_total} ping (${pct}%)</span>`;
      }
      const checksHtml = (t.checks || [])
        .map((c) => `
          <div class="check-row">
            <span class="check-name">${escapeHtml(c.name)}</span>
            <span class="check-symbol check-symbol-${c.status}">${escapeHtml(c.symbol)}</span>
          </div>
        `)
        .join("");
      const latency = t.latency_ms != null ? t.latency_ms + "ms" : "-";
      const updated = fmtRelative(now, t.last_refresh ? Date.parse(t.last_refresh) : null);
      const uptime = t.uptime_pct != null ? t.uptime_pct.toFixed(2) + "%" : "-";
      const versionHtml = t.version ? `<span class="tile-version">DHIS2  ${escapeHtml(t.version)}</span>` : "";
      return `
        <div class="tile ${cls}">
          <div class="tile-title">
            <span class="tile-name">${escapeHtml(t.name)}</span>
            ${versionHtml}
          </div>
          <div class="tile-url">${escapeHtml(t.url)}</div>
          <div class="pillrow">
            <span class="${pillCls}">${t.worst_status.toUpperCase()}</span>
            <span class="summary">${t.ok_count}/${t.total_count} checks</span>
            ${pingLine}
          </div>
          <div class="checks-header">Checks</div>
          <div class="checks">${checksHtml}</div>
          <div class="stats">
            <div>
              <div class="stat-label">latency</div>
              <div class="stat-value">${latency}</div>
            </div>
            <div>
              <div class="stat-label">updated</div>
              <div class="stat-value">${updated}</div>
            </div>
            <div>
              <div class="stat-label">uptime</div>
              <div class="stat-value">${uptime}</div>
            </div>
          </div>
        </div>
      `;
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, (c) =>
        ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]
      );
    }

    let lastState = null;

    function render(state) {
      lastState = state;
      const grid = document.getElementById("grid");
      const cols = columnsFor(state.tiles.length);
      grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
      const rows = Math.max(1, Math.ceil(state.tiles.length / cols));
      grid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
      const now = Date.now();
      grid.innerHTML = state.tiles.map((t) => tileHtml(t, now)).join("");

      document.getElementById("hdr-count").textContent = state.instance_count + " instance(s)";
      document.getElementById("hdr-alerts").textContent = "alerts " + (state.alerts_enabled ? "ON" : "OFF");
      document.getElementById("hdr-interval").textContent = "refresh every " + Math.floor(state.interval_s) + "s";

      const lastRefresh = state.last_refresh ? Date.parse(state.last_refresh) : null;
      const status = document.getElementById("last-refresh");
      if (!lastRefresh) {
        status.textContent = "awaiting first refresh ...";
        status.className = "stale";
      } else {
        const ageMs = now - lastRefresh;
        const stale = ageMs > state.interval_s * 1000 * 2;
        status.className = stale ? "stale" : "ok";
        status.textContent = "last refresh " + fmtRelative(now, lastRefresh);
      }
    }

    async function poll() {
      try {
        const r = await fetch("/api/state", { cache: "no-store" });
        if (!r.ok) return;
        const state = await r.json();
        render(state);
      } catch (e) {
        // Network blip - try again next tick.
      }
    }

    function tickClock() {
      const d = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      document.getElementById("clock").textContent =
        pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
      // Also refresh the relative-time labels so "updated 12s ago" advances.
      if (lastState) render(lastState);
    }

    setInterval(poll, 5000);     // pull the latest cached snapshot from the server
    setInterval(tickClock, 1000); // tick the wall clock + relative timestamps
    poll();
    tickClock();

    // ------------------------------------------------------------------
    // Command palette + keyboard shortcuts
    // ------------------------------------------------------------------
    const COMMANDS = [
      {
        id: "refresh",
        label: "Refresh now",
        hint: "r",
        run: () => { poll(); },
      },
      {
        id: "fullscreen",
        label: "Toggle fullscreen",
        hint: "f",
        run: () => {
          if (document.fullscreenElement) {
            document.exitFullscreen?.();
          } else {
            document.documentElement.requestFullscreen?.();
          }
        },
      },
      {
        id: "open-repo",
        label: "Open GitHub repository",
        hint: "",
        run: () => window.open("https://github.com/dhis2-chap/chap-checker", "_blank", "noopener"),
      },
      {
        id: "open-docs",
        label: "Open documentation",
        hint: "",
        run: () => window.open("https://dhis2-chap.github.io/chap-checker/", "_blank", "noopener"),
      },
    ];

    const paletteEl = document.getElementById("palette-backdrop");
    const inputEl = document.getElementById("palette-input");
    const listEl = document.getElementById("palette-list");
    let activeIndex = 0;
    let filtered = COMMANDS;

    function openPalette() {
      paletteEl.classList.add("open");
      inputEl.value = "";
      filtered = COMMANDS;
      activeIndex = 0;
      renderPalette();
      // Defer focus until after the modal becomes visible.
      requestAnimationFrame(() => inputEl.focus());
    }
    function closePalette() {
      paletteEl.classList.remove("open");
    }
    function renderPalette() {
      listEl.innerHTML = filtered
        .map((c, i) => `
          <li class="palette-item ${i === activeIndex ? "active" : ""}" data-i="${i}">
            <span class="label">${escapeHtml(c.label)}</span>
            ${c.hint ? `<span class="hint">${escapeHtml(c.hint)}</span>` : ""}
          </li>
        `)
        .join("");
    }
    function applyFilter() {
      const q = inputEl.value.trim().toLowerCase();
      filtered = q
        ? COMMANDS.filter((c) => c.label.toLowerCase().includes(q))
        : COMMANDS;
      activeIndex = 0;
      renderPalette();
    }
    function runActive() {
      const cmd = filtered[activeIndex];
      if (cmd) {
        closePalette();
        cmd.run();
      }
    }

    inputEl.addEventListener("input", applyFilter);
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closePalette(); e.preventDefault(); }
      else if (e.key === "ArrowDown") {
        activeIndex = Math.min(filtered.length - 1, activeIndex + 1);
        renderPalette();
        e.preventDefault();
      } else if (e.key === "ArrowUp") {
        activeIndex = Math.max(0, activeIndex - 1);
        renderPalette();
        e.preventDefault();
      } else if (e.key === "Enter") {
        runActive();
        e.preventDefault();
      }
    });
    listEl.addEventListener("click", (e) => {
      const item = e.target.closest(".palette-item");
      if (!item) return;
      activeIndex = Number(item.dataset.i);
      runActive();
    });
    paletteEl.addEventListener("click", (e) => {
      // Click on backdrop (not the inner card) closes.
      if (e.target === paletteEl) closePalette();
    });

    // Global hotkeys. Ignored when an input/textarea has focus, except
    // for Escape inside the palette which is handled above.
    document.addEventListener("keydown", (e) => {
      const isPaletteOpen = paletteEl.classList.contains("open");
      const target = e.target;
      const inField = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");

      // Ctrl/Cmd + K toggles the palette regardless of focus.
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (isPaletteOpen) closePalette();
        else openPalette();
        return;
      }
      if (inField || isPaletteOpen) return;
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        poll();
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        COMMANDS.find((c) => c.id === "fullscreen")?.run();
      }
    });
  </script>
</body>
</html>
"""
