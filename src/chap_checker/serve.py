"""HTTP server for chap-checker - `chap-checker serve`.

Long-running daemon. Runs the checks on a background loop via
`DashboardServer` (shared with the TUI), exposes the state over JSON at
`GET /api/state`, and by default serves a browser dashboard at `GET /`
with the same layout and palette as the Textual TUI. The TUI's
`--connect` mode and any browser pointed at this server see the exact
same snapshot.

Architecture:

- Initial GET `/` serves a single static HTML page with embedded CSS
  and a tiny JS poller. No build step. Disable with `--no-ui` for a
  headless API-only daemon.
- GET `/api/state` returns the current snapshot as JSON. Same shape the
  TUI's `--connect` mode consumes.
- POST `/api/reload` re-reads the config file and applies it in place.
- A background `asyncio.create_task` re-runs `run_targets` every
  `interval_s` seconds, dispatches alerts (when `--alerts`), and
  updates the shared state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from chap_checker.config import CheckerConfig
from chap_checker.daemon import DashboardServer
from chap_checker.logging import get_logger
from chap_checker.runner import TargetEntry

_log = get_logger("web")
_access_log = get_logger("web.access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One info-level log line per HTTP request.

    Carries the Apache-combined-style fields (client IP, method, path,
    HTTP version, status code, content-length, Referer, User-Agent) plus
    a `duration_ms` for slow-endpoint spotting. Replaces uvicorn's default
    access log so the log surface has one consistent format.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        client = request.client.host if request.client else "-"
        http_version = request.scope.get("http_version", "1.1")
        bytes_sent = response.headers.get("content-length", "-")
        ua = request.headers.get("user-agent", "-")
        _access_log.info(
            '%s "%s %s HTTP/%s" %d %s %.1fms ua="%s"',
            client,
            request.method,
            request.url.path,
            http_version,
            response.status_code,
            bytes_sent,
            elapsed_ms,
            ua,
        )
        return response


def make_app(server: DashboardServer, ui_enabled: bool = True) -> FastAPI:
    """Build the FastAPI app, wiring the background refresh task into its lifespan.

    With `ui_enabled=False` the browser dashboard is skipped — `GET /` returns
    404 and the static-file mount is not registered. The JSON API at
    `/api/*` is unaffected; use this for headless deployments where only
    the TUI `--connect` clients or external scrapers consume the state.
    """

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
    app.add_middleware(AccessLogMiddleware)

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(server.snapshot().model_dump(mode="json"))

    @app.post("/api/reload")
    async def api_reload() -> JSONResponse:
        """Re-read the config file and apply targets / cfg in place.

        Returns 200 with the delta on success; 400 with the error message
        when the config can't be parsed or is invalid (does not crash the
        background refresh loop).
        """
        try:
            added, removed = server.reload()
        except FileNotFoundError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=404)
        except Exception as exc:  # noqa: BLE001 - surface any parse / validation error
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
        return JSONResponse(
            {
                "status": "ok",
                "instance_count": len(server.targets),
                "added": sorted(added),
                "removed": sorted(removed),
            }
        )

    if ui_enabled:
        # The React SPA + Babel-standalone wiring lives next to this module
        # inside the chap_checker package (`web_ui/`). StaticFiles serves
        # index.html on GET / and every vendor/src/* asset directly. Bundling
        # the assets inside the package means they ship with both editable
        # installs and built wheels (uv_build picks up everything under
        # src/chap_checker/ automatically).
        web_ui = _web_ui_dir()

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(web_ui / "index.html")

        app.mount("/", StaticFiles(directory=str(web_ui), html=False), name="web-ui")

    return app


def _web_ui_dir() -> Path:
    """Return the bundled `web_ui/` directory, raising if missing.

    The directory sits next to `serve.py` inside the `chap_checker`
    package. Resolving it via `__file__` keeps it on the same path
    in editable installs, built wheels, and zipped sdists.
    """
    candidate = Path(__file__).resolve().parent / "web_ui"
    if not (candidate / "index.html").exists():
        raise FileNotFoundError(
            f"web_ui/index.html not found at {candidate}. "
            "The browser dashboard expects the React assets to ship alongside the package."
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
    config_path: Path | None = None,
    ui_enabled: bool = True,
) -> None:
    """Launch the chap-checker server on `host:port`.

    Long-running daemon that runs the check loop, fires alerts (when
    `alerts_enabled`), exposes JSON state at `/api/state`, and by
    default serves a browser dashboard at `/`. Pass `ui_enabled=False`
    (or `--no-ui` on the CLI) for a headless API-only deployment.

    Use `host="0.0.0.0"` to expose on the local network (e.g. for a TV
    in the office, or so a remote `chap-checker tui --connect URL` can
    point at it). The server has no authentication; either keep it on
    the loopback interface or put it behind a reverse proxy with
    network-level access controls.
    """
    server = DashboardServer(
        targets=targets,
        cfg=cfg,
        state_path=state_path,
        interval_s=interval_s,
        alerts_enabled=alerts_enabled,
        config_path=config_path,
    )
    app = make_app(server, ui_enabled=ui_enabled)
    # Late import - uvicorn is heavyish and only needed when serving.
    import uvicorn

    # The CLI defaults the chap_checker logger to WARNING (so `verify` /
    # `tui` stay quiet). For the long-running daemon we want INFO so the
    # access-log middleware and per-refresh lines are visible; only stay
    # at the quieter level when --debug already promoted us to DEBUG.
    pkg_log = logging.getLogger("chap_checker")
    if pkg_log.level == logging.NOTSET or pkg_log.level > logging.INFO:
        pkg_log.setLevel(logging.INFO)

    surface = "dashboard + API" if ui_enabled else "API only (--no-ui)"
    _log.info("serving %s on http://%s:%d", surface, host, port)
    if config_path is not None:
        _log.info("config: %s", config_path)
    _log.info("interval: %.1fs; alerts: %s", interval_s, "on" if alerts_enabled else "off")
    # `access_log=False` disables uvicorn's built-in per-request log so
    # AccessLogMiddleware is the single source of those lines. Uvicorn's
    # own INFO startup banner ("Started server process …") still shows.
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


__all__: list[str] = [
    "make_app",
    "run",
]
