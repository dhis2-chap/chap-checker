r"""Capture PNGs of the browser dashboard against a live ``serve`` daemon.

Uses the Playwright async API (so this can run in CI without the MCP server),
drives the auth flow end-to-end:

1. Navigate to the daemon URL, screenshot the login modal.
2. Type the token, click *Sign in*.
3. Wait for live data to render, screenshot the signed-in dashboard.
4. Click the *Sign out* button, wait for the modal to reappear, screenshot it.

``--palette`` also captures the Ctrl+K command palette over the dashboard.

``--states`` also captures the connection / freshness states. The daemon
keeps running; the script simulates the failures by intercepting
``/api/state`` in the browser and fast-forwarding the page clock:

- ``web-dashboard-loading``: first ``/api/state`` never answers (loading panel).
- ``web-dashboard-unreachable``: first ``/api/state`` fails (no data yet).
- ``web-dashboard-reconnecting``: a poll fails after live data arrived.
- ``web-dashboard-stale``: polls keep failing past the stale threshold.
- ``web-dashboard-stale-idle``: the daemon answers but its ``last_refresh`` is
  ten minutes old (stalled check loop).

and then checks that the dashboard recovers to ``live`` once polls succeed.

Requires ``playwright`` (a dev dep) plus the chromium binary::

    uv add --dev playwright
    uv run playwright install chromium

Then::

    uv run python scripts/capture_browser.py \\
        --url http://127.0.0.1:8765 \\
        --token dev-secret-12345 \\
        --output-dir docs/assets

writes ``web-dashboard-login.png``, ``web-dashboard.png`` and
``web-dashboard-signed-out.png`` into ``--output-dir`` (plus the optional
ones above). ``--suffix=-dhis2`` (with ``=``, as the value starts with a
dash) appends to every file stem, for the
committed dhis2-theme captures (the theme itself comes from the daemon's
``[ui].theme``). ``--color-scheme`` pins ``prefers-color-scheme``; the
dashboard's look must not depend on it, so capture both when checking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from playwright.async_api import Page, Route, async_playwright

ColorScheme = Literal["light", "dark", "no-preference"]


async def _wait_state(page: Page, state: str, timeout: int = 15_000) -> None:
    """Wait until app.jsx reports the given freshness state on <html>."""
    await page.wait_for_selector(f'html[data-ck-freshness="{state}"]', state="attached", timeout=timeout)


async def _shot(page: Page, output_dir: Path, stem: str, suffix: str) -> None:
    path = output_dir / f"{stem}{suffix}.png"
    await page.screenshot(path=str(path), full_page=False)
    print(f"wrote {path}")


async def _capture_states(page: Page, output_dir: Path, suffix: str) -> None:
    """Capture loading / reconnecting / stale states and verify recovery."""
    context = page.context

    # Loading: a fresh tab whose first /api/state never answers. The token
    # is already in localStorage (shared per context), so no modal.
    pending: list[Route] = []

    async def hang(route: Route) -> None:
        pending.append(route)

    loading = await context.new_page()
    await loading.route("**/api/state", hang)
    await loading.goto(page.url, wait_until="domcontentloaded")
    await _wait_state(loading, "connecting")
    await loading.wait_for_function("() => window.CK_THEME_BOOTSTRAPPED === true", timeout=10_000)
    await loading.wait_for_timeout(300)
    await _shot(loading, output_dir, "web-dashboard-loading", suffix)
    await loading.close()

    # Unreachable: the first /api/state fails outright.
    unreachable = await context.new_page()
    await unreachable.route("**/api/state", lambda route: route.abort())
    await unreachable.goto(page.url, wait_until="domcontentloaded")
    await _wait_state(unreachable, "unreachable")
    await unreachable.wait_for_function("() => window.CK_THEME_BOOTSTRAPPED === true", timeout=10_000)
    await unreachable.wait_for_timeout(300)
    await _shot(unreachable, output_dir, "web-dashboard-unreachable", suffix)
    await unreachable.close()

    # Reconnecting: polls start failing after live data arrived.
    await page.route("**/api/state", lambda route: route.abort())
    await page.clock.fast_forward(8_000)
    await _wait_state(page, "reconnecting")
    await page.wait_for_timeout(300)
    await _shot(page, output_dir, "web-dashboard-reconnecting", suffix)

    # Stale: still failing well past max(4 * poll, 30s).
    await page.clock.fast_forward("01:00")
    await _wait_state(page, "stale")
    await page.wait_for_timeout(500)
    await _shot(page, output_dir, "web-dashboard-stale", suffix)

    # Recovery: polls succeed again -> back to live without a reload.
    await page.unroute("**/api/state")
    await page.clock.fast_forward(8_000)
    await _wait_state(page, "live")
    print("recovered to live after polls resumed")

    # Stalled daemon: answers, but last_refresh is ten minutes old.
    async def stalled(route: Route) -> None:
        response = await route.fetch()
        body = await response.json()
        old = datetime.now(UTC) - timedelta(minutes=10)
        body["last_refresh"] = old.isoformat()
        for tile in body.get("tiles", []):
            tile["last_refresh"] = old.isoformat()
        await route.fulfill(response=response, body=json.dumps(body))

    await page.route("**/api/state", stalled)
    await page.clock.fast_forward(8_000)
    await _wait_state(page, "stale")
    await page.wait_for_timeout(500)
    await _shot(page, output_dir, "web-dashboard-stale-idle", suffix)
    await page.unroute("**/api/state")
    await page.clock.fast_forward(8_000)
    await _wait_state(page, "live")

    for route in pending:
        await route.abort()


async def _capture(
    url: str,
    token: str,
    output_dir: Path,
    width: int,
    height: int,
    *,
    suffix: str,
    color_scheme: ColorScheme | None,
    palette: bool,
    states: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            color_scheme=color_scheme,
        )
        page = await context.new_page()
        if states:
            # Fake timers that still flow in real time, so the stale
            # scenarios can jump ahead instead of sleeping for a minute.
            await page.clock.install()
        await page.goto(url, wait_until="networkidle")

        # Login modal: input#ck-token + Sign in button.
        await page.wait_for_selector("#ck-token", timeout=10_000)
        await _shot(page, output_dir, "web-dashboard-login", suffix)

        await page.fill("#ck-token", token)
        await page.get_by_role("button", name="Sign in").click()

        # Live tiles render once /api/state returns 200; app.jsx then marks
        # <html data-ck-freshness="live">.
        await _wait_state(page, "live")
        await page.wait_for_selector("text=Sign out", timeout=10_000)
        # The browser bootstraps the daemon's `[ui].theme` from /api/state's
        # `ui_theme` field after sign-in. `window.CK_THEME_BOOTSTRAPPED` is
        # set inside the applyTheme useEffect once it has run post-bootstrap;
        # waiting for it is the deterministic signal that the theme reflow
        # has finished - replaces the previous "sleep 3500ms and hope".
        await page.wait_for_function(
            "() => window.CK_THEME_BOOTSTRAPPED === true",
            timeout=10_000,
        )
        # Tiny extra settle for the uptime strip's animated CSS transitions.
        await page.wait_for_timeout(500)
        await _shot(page, output_dir, "web-dashboard", suffix)

        if palette:
            # Park the pointer on the header so it doesn't hover-select a
            # palette row in the capture.
            await page.mouse.move(5, 5)
            await page.keyboard.press("Control+k")
            await page.wait_for_timeout(400)
            await _shot(page, output_dir, "web-dashboard-palette", suffix)
            await page.keyboard.press("Escape")

        if states:
            await _capture_states(page, output_dir, suffix)

        # Sign out -> modal reappears.
        await page.get_by_role("button", name="Sign out").click()
        await page.wait_for_selector("#ck-token", timeout=10_000)
        await _shot(page, output_dir, "web-dashboard-signed-out", suffix)

        await context.close()
        await browser.close()


def main() -> None:
    """Parse args and run the capture."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--url",
        required=True,
        help="Dashboard URL, e.g. http://127.0.0.1:8765",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="Bearer token to sign in with",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/assets"),
        help="Where to write the PNGs (default: docs/assets)",
    )
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument(
        "--suffix",
        default="",
        help="Appended to every file stem, e.g. --suffix=-dhis2 -> web-dashboard-dhis2.png",
    )
    parser.add_argument(
        "--color-scheme",
        choices=["light", "dark", "no-preference"],
        default=None,
        help="Emulated prefers-color-scheme (default: Playwright's default)",
    )
    parser.add_argument("--palette", action="store_true", help="Also capture the Ctrl+K command palette")
    parser.add_argument(
        "--states",
        action="store_true",
        help="Also capture loading / unreachable / reconnecting / stale states and verify recovery",
    )
    args = parser.parse_args()
    asyncio.run(
        _capture(
            args.url,
            args.token,
            args.output_dir,
            args.width,
            args.height,
            suffix=args.suffix,
            color_scheme=args.color_scheme,
            palette=args.palette,
            states=args.states,
        )
    )


if __name__ == "__main__":
    main()
