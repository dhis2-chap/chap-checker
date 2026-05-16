r"""Capture PNGs of the browser dashboard against a live ``serve`` daemon.

Uses the Playwright async API (so this can run in CI without the MCP server),
drives the auth flow end-to-end:

1. Navigate to the daemon URL, screenshot the login modal.
2. Type the token, click *Sign in*.
3. Wait for tiles to render, screenshot the signed-in dashboard.
4. Click the *Sign out* button, wait for the modal to reappear, screenshot it.

Requires ``playwright`` (a dev dep) plus the chromium binary::

    uv add --dev playwright
    uv run playwright install chromium

Then::

    uv run python scripts/capture_browser.py \\
        --url http://127.0.0.1:8765 \\
        --token dev-secret-12345 \\
        --output-dir docs/assets

writes three PNGs into ``--output-dir``: ``web-dashboard-login.png``,
``web-dashboard.png``, ``web-dashboard-signed-out.png``.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def _capture(url: str, token: str, output_dir: Path, width: int, height: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": width, "height": height})
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")

        # Login modal: input#ck-token + Sign in button.
        await page.wait_for_selector("#ck-token", timeout=10_000)
        login_path = output_dir / "web-dashboard-login.png"
        await page.screenshot(path=str(login_path), full_page=False)
        print(f"wrote {login_path}")

        await page.fill("#ck-token", token)
        await page.get_by_role("button", name="Sign in").click()

        # Tiles render once /api/state returns 200. Wait for a tile element or
        # the absence of the modal - we key on the Sign out button.
        await page.wait_for_selector("text=Sign out", timeout=10_000)
        # Give the polling cycle a moment to settle the uptime strip.
        await page.wait_for_timeout(1500)
        dashboard_path = output_dir / "web-dashboard.png"
        await page.screenshot(path=str(dashboard_path), full_page=False)
        print(f"wrote {dashboard_path}")

        # Sign out -> modal reappears.
        await page.get_by_role("button", name="Sign out").click()
        await page.wait_for_selector("#ck-token", timeout=10_000)
        signed_out_path = output_dir / "web-dashboard-signed-out.png"
        await page.screenshot(path=str(signed_out_path), full_page=False)
        print(f"wrote {signed_out_path}")

        await context.close()
        await browser.close()


def main() -> None:
    """Parse args and run the capture."""
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args()
    asyncio.run(_capture(args.url, args.token, args.output_dir, args.width, args.height))


if __name__ == "__main__":
    main()
