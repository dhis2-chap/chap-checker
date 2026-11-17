"""TUI manual refresh (connect mode) and config reload error handling.

Manual refresh (`r` / command palette) in `--connect` mode must ask the
daemon to re-run its checks via `POST /api/refresh` before fetching
`/api/state`; the periodic poll only GETs state. Config reload must
build the whole replacement (config + runtime targets) before swapping
anything in, so an unset credential env var surfaces as a toast instead
of crashing the app.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from chap_checker.checks.base import Status
from chap_checker.config import load_config
from chap_checker.daemon import DashboardServer, DashboardState, TileModel

REMOTE = "http://remote.example:8765"


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


def _state_get_mock() -> AsyncMock:
    canned = _canned_state()

    async def _get(url: str, *_args: Any, **_kwargs: Any) -> httpx2.Response:
        request = httpx2.Request("GET", url)
        if url.endswith("/api/state"):
            return httpx2.Response(200, content=canned.model_dump_json().encode(), request=request)
        return httpx2.Response(404, request=request)

    return AsyncMock(side_effect=_get)


def _get_urls(mock: AsyncMock) -> list[str]:
    return [call.args[0] for call in mock.await_args_list]


def _post_mock(status_code: int = 200, exc: Exception | None = None) -> AsyncMock:
    async def _post(url: str, *_args: Any, **_kwargs: Any) -> httpx2.Response:
        if exc is not None:
            raise exc
        return httpx2.Response(status_code, json={"status": "ok"}, request=httpx2.Request("POST", url))

    return AsyncMock(side_effect=_post)


def test_connect_mode_manual_refresh_posts_then_fetches_state() -> None:
    from chap_checker.dashboard import DashboardApp

    mock_get = _state_get_mock()
    mock_post = _post_mock()

    async def _run() -> None:
        with (
            patch.object(httpx2.AsyncClient, "get", new=mock_get),
            patch.object(httpx2.AsyncClient, "post", new=mock_post),
        ):
            app = DashboardApp(interval_s=300.0, connect_url=REMOTE, connect_token="tok")
            async with app.run_test(headless=True) as pilot:
                await pilot.pause()
                # The startup poll only GETs state; it never triggers a daemon run.
                assert mock_post.await_count == 0
                gets_before = len(_get_urls(mock_get))
                await app.action_refresh()
                await pilot.pause()
                assert mock_post.await_count == 1
                assert mock_post.await_args is not None
                assert mock_post.await_args.args[0] == f"{REMOTE}/api/refresh"
                assert _get_urls(mock_get)[gets_before:] == [f"{REMOTE}/api/state"]
                # The persistent client carries the bearer token on the POST too.
                assert app._http_client is not None
                assert app._http_client.headers.get("Authorization") == "Bearer tok"
                assert "alpha" in app.tiles

    asyncio.run(_run())


def test_connect_mode_periodic_poll_does_not_post_refresh() -> None:
    from chap_checker.dashboard import DashboardApp

    mock_get = _state_get_mock()
    mock_post = _post_mock()

    async def _run() -> None:
        with (
            patch.object(httpx2.AsyncClient, "get", new=mock_get),
            patch.object(httpx2.AsyncClient, "post", new=mock_post),
        ):
            app = DashboardApp(interval_s=300.0, connect_url=REMOTE)
            async with app.run_test(headless=True) as pilot:
                await app._poll()
                await pilot.pause()
                assert mock_post.await_count == 0
                assert f"{REMOTE}/api/state" in _get_urls(mock_get)

    asyncio.run(_run())


def test_connect_mode_manual_refresh_401_paints_auth_banner() -> None:
    from chap_checker.dashboard import DashboardApp, DisconnectBanner

    mock_get = _state_get_mock()
    mock_post = _post_mock(status_code=401)

    async def _run() -> None:
        with (
            patch.object(httpx2.AsyncClient, "get", new=mock_get),
            patch.object(httpx2.AsyncClient, "post", new=mock_post),
        ):
            app = DashboardApp(interval_s=300.0, connect_url=REMOTE, connect_token="wrong")
            async with app.run_test(headless=True) as pilot:
                await pilot.pause()
                gets_before = len(_get_urls(mock_get))
                await app.action_refresh()
                await pilot.pause()
                banner = app.query_one("#banner", DisconnectBanner)
                assert banner.has_class("visible")
                assert "check the token value" in str(banner.render())
                # No state fetch after a rejected refresh.
                assert len(_get_urls(mock_get)) == gets_before

    asyncio.run(_run())


def test_connect_mode_manual_refresh_401_without_token_pushes_modal() -> None:
    from chap_checker.dashboard import DashboardApp, TokenPromptScreen

    mock_post = _post_mock(status_code=401)
    request = httpx2.Request("GET", f"{REMOTE}/api/state")
    mock_get = AsyncMock(return_value=httpx2.Response(401, text="no", request=request))

    async def _run() -> None:
        with (
            patch.object(httpx2.AsyncClient, "get", new=mock_get),
            patch.object(httpx2.AsyncClient, "post", new=mock_post),
        ):
            app = DashboardApp(interval_s=300.0, connect_url=REMOTE)
            async with app.run_test(headless=True) as pilot:
                # Mark the startup modal as already handled so this test
                # isolates the POST's own 401 path.
                await pilot.pause()
                if isinstance(app.screen, TokenPromptScreen):
                    app.screen.dismiss(None)
                    await pilot.pause()
                app._token_prompted = False
                await app.action_refresh()
                await pilot.pause()
                assert isinstance(app.screen, TokenPromptScreen)

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("post", "expected"),
    [
        (_post_mock(status_code=500), "HTTP 500"),
        (_post_mock(exc=httpx2.ConnectError("boom")), "boom"),
    ],
)
def test_connect_mode_manual_refresh_failure_notifies(post: AsyncMock, expected: str) -> None:
    from chap_checker.dashboard import DashboardApp

    mock_get = _state_get_mock()

    async def _run() -> None:
        with patch.object(httpx2.AsyncClient, "get", new=mock_get), patch.object(httpx2.AsyncClient, "post", new=post):
            app = DashboardApp(interval_s=300.0, connect_url=REMOTE, connect_token="tok")
            async with app.run_test(headless=True) as pilot:
                await pilot.pause()
                notify = MagicMock()
                app.notify = notify  # type: ignore[method-assign]
                await app.action_refresh()
                await pilot.pause()
                assert notify.call_count == 1
                message = notify.call_args.args[0]
                assert message.startswith("Refresh failed:")
                assert expected in message
                assert notify.call_args.kwargs.get("severity") == "error"

    asyncio.run(_run())


# ---------- Local-mode reload ----------


_GOOD_TOML = """
[instances.alpha]
url = "https://alpha.example"
username = "u"
password_env = "CHAP_TEST_RELOAD_PW"
"""

_BAD_TOML = """
[instances.alpha]
url = "https://alpha.example"
username = "u"
password_env = "CHAP_TEST_RELOAD_PW"

[instances.beta]
url = "https://beta.example"
username = "u"
password_env = "CHAP_TEST_RELOAD_UNSET"
"""


def test_reload_with_unset_credential_env_keeps_old_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chap_checker.dashboard import DashboardApp

    monkeypatch.setenv("CHAP_TEST_RELOAD_PW", "pw")
    monkeypatch.delenv("CHAP_TEST_RELOAD_UNSET", raising=False)
    config_path = tmp_path / "chap-checker.toml"
    config_path.write_text(_GOOD_TOML)
    cfg = load_config(config_path)
    targets = [entry.to_target_entry(name, default_retry_policy=cfg.retry) for name, entry in cfg.instances.items()]

    async def _run() -> None:
        with patch.object(DashboardServer, "refresh_once", new=AsyncMock(return_value=None)):
            app = DashboardApp(targets=targets, cfg=cfg, config_path=config_path, interval_s=300.0)
            async with app.run_test(headless=True) as pilot:
                await pilot.pause()
                assert app.server is not None
                old_targets = app.server.targets
                old_cfg = app.server.cfg
                notify = MagicMock()
                app.notify = notify  # type: ignore[method-assign]

                config_path.write_text(_BAD_TOML)
                await app.action_reload()
                await pilot.pause()

                assert app.is_running
                assert app.server.targets is old_targets
                assert app.server.cfg is old_cfg
                assert notify.call_count == 1
                message = notify.call_args.args[0]
                assert message.startswith("Reload failed:")
                assert "CHAP_TEST_RELOAD_UNSET" in message
                assert notify.call_args.kwargs.get("severity") == "error"

    asyncio.run(_run())


def test_reload_applies_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chap_checker.dashboard import DashboardApp

    monkeypatch.setenv("CHAP_TEST_RELOAD_PW", "pw")
    monkeypatch.setenv("CHAP_TEST_RELOAD_UNSET", "now-set")
    config_path = tmp_path / "chap-checker.toml"
    config_path.write_text(_GOOD_TOML)
    cfg = load_config(config_path)
    targets = [entry.to_target_entry(name, default_retry_policy=cfg.retry) for name, entry in cfg.instances.items()]

    async def _run() -> None:
        with patch.object(DashboardServer, "refresh_once", new=AsyncMock(return_value=None)):
            app = DashboardApp(targets=targets, cfg=cfg, config_path=config_path, interval_s=300.0)
            async with app.run_test(headless=True) as pilot:
                await pilot.pause()
                config_path.write_text(_BAD_TOML)
                await app.action_reload()
                await pilot.pause()
                assert app.server is not None
                assert {t.name for t in app.server.targets} == {"alpha", "beta"}

    asyncio.run(_run())
