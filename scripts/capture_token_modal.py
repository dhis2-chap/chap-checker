r"""Capture an SVG of the TUI auth-token modal against a live ``serve`` daemon.

The TUI client (``chap-checker tui --connect URL``) renders a
``TokenPromptScreen`` modal when the daemon returns 401. The modal MUST pick up
the daemon's ``[ui].theme`` before paint, otherwise it briefly flashes the
default phosphor theme. This script reproduces that flow headlessly so the
rendered modal can be inspected as an SVG.

Boot the daemon first, then::

    uv run python scripts/capture_token_modal.py \\
        --connect http://127.0.0.1:8765 \\
        --output docs/assets/tui-token-modal.svg
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from chap_checker.dashboard import DashboardApp, TokenPromptScreen


async def _capture(connect_url: str, output: Path, wait_s: float) -> None:
    app = DashboardApp(interval_s=300.0, connect_url=connect_url)
    async with app.run_test(size=(180, 50), headless=True) as pilot:
        # on_mount runs _probe_remote_theme then triggers the first refresh,
        # which 401's and pushes TokenPromptScreen. Give that enough wall time.
        await pilot.pause(delay=wait_s)
        if not isinstance(app.screen, TokenPromptScreen):
            await app.action_refresh()
            await pilot.pause(delay=wait_s)
        output.parent.mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(output))
        print(f"wrote {output}; active screen: {type(app.screen).__name__}; theme: {app.theme}")


def main() -> None:
    """Parse args and run the capture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connect",
        required=True,
        help="Daemon base URL, e.g. http://127.0.0.1:8765",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/assets/tui-token-modal.svg"),
        help="Output SVG path (default: docs/assets/tui-token-modal.svg)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=1.5,
        help="Seconds to wait between mount and capture (default 1.5)",
    )
    args = parser.parse_args()
    asyncio.run(_capture(args.connect, args.output, args.wait))


if __name__ == "__main__":
    main()
