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
import hmac
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from chap_checker.config import CheckerConfig
from chap_checker.daemon import DashboardServer
from chap_checker.logging import get_logger
from chap_checker.runner import TargetEntry

_log = get_logger("serve")
_access_log = get_logger("serve.access")


def _make_auth_dependency(server: DashboardServer) -> Callable[..., Awaitable[None]]:
    """Return a FastAPI dependency that gates protected routes on the current bearer token.

    Reads `server.resolved_auth_token` at request time (not at app-build
    time) so that POST /api/reload, which re-resolves the token from the
    new `[auth]` block, takes effect immediately. Token=None means auth
    is currently disabled - the dependency is a no-op.

    Comparison uses `hmac.compare_digest` so a wrong token can't be
    brute-forced by timing.
    """

    async def require_auth(
        authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
    ) -> None:
        token = server.resolved_auth_token
        if token is None:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(provided, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bad token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_auth


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


def make_app(
    server: DashboardServer,
    ui_enabled: bool = True,
    auth_token: str | None = None,
) -> FastAPI:
    """Build the FastAPI app, wiring the background refresh task into its lifespan.

    With `ui_enabled=False` the browser dashboard is skipped — `GET /` returns
    404 and the static-file mount is not registered. The JSON API at
    `/api/*` is unaffected; use this for headless deployments where only
    the TUI `--connect` clients or external scrapers consume the state.

    `auth_token`, when set, seeds the server's resolved-token state so
    that `/api/state` and `/api/reload` start out gated by a bearer-token
    check. The check itself reads `server.resolved_auth_token` at
    request time, so subsequent POST /api/reload calls re-resolve from
    the new `[auth]` block and take effect immediately. The browser SPA
    + static assets stay unauthenticated so the login modal can render
    before a token exists (the SPA fetches `/api/state`, gets 401,
    prompts for the token, stores it in localStorage, retries).
    `auth_token=None` keeps the routes unauthenticated initially,
    matching 0.6.x behaviour - reload can still enable auth later.
    """
    server.resolved_auth_token = auth_token

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

    require_auth = _make_auth_dependency(server)
    protected: list[Any] = [Depends(require_auth)]

    @app.get("/api/state", dependencies=protected)
    async def api_state() -> JSONResponse:
        return JSONResponse(server.snapshot().model_dump(mode="json"))

    @app.post("/api/reload", dependencies=protected)
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

    @app.get("/api/auth", include_in_schema=False)
    async def api_auth_status() -> JSONResponse:
        """Lightweight unprotected hint so the SPA can show / hide the login modal.

        Returns `{"required": bool, "ui_theme": str, "ui_title": str}`.
        - `required`: whether `/api/*` routes need a bearer token.
        - `ui_theme` + `ui_title`: the `[ui]` config the SPA otherwise
          only sees in the protected `/api/state` payload. Surfacing
          them here lets the login modal apply the configured theme on
          first paint instead of flashing phosphor-green before the
          artifact has data. No secrets leak - just CSS preferences.
        """
        return JSONResponse(
            {
                "required": server.resolved_auth_token is not None,
                "ui_theme": server.cfg.ui.theme,
                "ui_title": server.cfg.ui.title,
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
    auth_token: str | None = None,
) -> None:
    """Launch the chap-checker server on `host:port`.

    Long-running daemon that runs the check loop, fires alerts (when
    `alerts_enabled`), exposes JSON state at `/api/state`, and by
    default serves a browser dashboard at `/`. Pass `ui_enabled=False`
    (or `--no-ui` on the CLI) for a headless API-only deployment.

    `auth_token` (resolved from the TOML's `[auth]` block by the CLI)
    protects `/api/*` with a bearer-token check. `None` (the default)
    leaves the daemon unauthenticated, matching 0.6.x behaviour. When
    `host` is non-loopback (`0.0.0.0`, a LAN address, ...) AND auth is
    off, a startup WARNING is emitted - "the daemon is reachable but
    has no credentials gating /api/state".
    """
    server = DashboardServer(
        targets=targets,
        cfg=cfg,
        state_path=state_path,
        interval_s=interval_s,
        alerts_enabled=alerts_enabled,
        config_path=config_path,
    )
    app = make_app(server, ui_enabled=ui_enabled, auth_token=auth_token)
    # Late import - uvicorn is heavyish and only needed when serving.
    import uvicorn

    # The CLI defaults the chap_checker logger to WARNING (so `verify` /
    # `tui` stay quiet). For the long-running daemon we want INFO so the
    # access-log middleware and per-refresh lines are visible; only stay
    # at the quieter level when --debug already promoted us to DEBUG.
    pkg_log = logging.getLogger("chap_checker")
    if pkg_log.level == logging.NOTSET or pkg_log.level > logging.INFO:
        pkg_log.setLevel(logging.INFO)

    # Route uvicorn's own loggers through the chap_checker handler so its
    # startup banner ("Started server process …") shares the same format
    # as our own lines instead of printing uvicorn's default `INFO:` style.
    pkg_handler = pkg_log.handlers[0] if pkg_log.handlers else None
    if pkg_handler is not None:
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            u = logging.getLogger(name)
            u.handlers[:] = [pkg_handler]
            u.propagate = False
            u.setLevel(logging.INFO)

    surface = "dashboard + API" if ui_enabled else "API only (--no-ui)"
    _log.info("serving %s on http://%s:%d", surface, host, port)
    if config_path is not None:
        _log.info("config: %s", config_path)
    _log.info(
        "interval: %.1fs; alerts: %s; auth: %s",
        interval_s,
        "on" if alerts_enabled else "off",
        "bearer-token" if auth_token else "off",
    )

    # Non-loopback bind without auth is the textbook "anyone on the LAN can
    # see your DHIS2 status" footgun. Don't refuse the bind (some operators
    # are behind a reverse proxy / VPN and don't want this fatal), but
    # nudge hard at startup.
    if auth_token is None and host not in {"127.0.0.1", "localhost", "::1"}:
        _log.warning(
            "serving on %s without [auth] - the daemon is reachable from outside loopback "
            "and /api/state is unauthenticated. Add an [auth] block to chap-checker.toml "
            "(see `chap-checker.toml.example`) or front the daemon with a reverse proxy.",
            host,
        )
    # `access_log=False` disables uvicorn's built-in per-request log so
    # AccessLogMiddleware is the single source of those lines.
    # `log_config=None` tells uvicorn not to re-configure logging on
    # startup, so its own loggers keep the chap_checker handler we wired
    # up above and its startup banner ("Started server process …") shares
    # our format instead of printing in uvicorn's default `INFO:` style.
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        log_config=None,
    )


__all__: list[str] = [
    "make_app",
    "run",
]
