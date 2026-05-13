"""Capture an SVG screenshot of the dashboard against a live config.

Used to regenerate ``docs/assets/dashboard.svg`` shown on the TUI docs page.
Requires real network access to the DHIS2 instances listed in the config.

Usage::

    uv run python scripts/capture_dashboard.py \
        --config chap-checker.toml \
        --output docs/assets/dashboard.svg \
        --wait 20 \
        --refreshes 5
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from chap_checker.config import load_config
from chap_checker.dashboard import DashboardApp


async def _capture(config_path: Path, output: Path, wait_s: float, refreshes: int) -> None:
    cfg = load_config(config_path)
    targets = [entry.to_target_entry(name) for name, entry in cfg.instances.items()]
    app = DashboardApp(
        targets=targets,
        cfg=cfg,
        config_path=config_path,
        state_path=None,
        # Disable auto-refresh - we drive the cycles manually below so the
        # uptime strip has visible history before we snap the screenshot.
        interval_s=9999.0,
        alerts_enabled=False,
    )
    async with app.run_test(size=(180, 50)) as pilot:
        # The dashboard's on_mount kicks off one refresh via call_after_refresh.
        # Wait long enough for it to land against real DHIS2 instances.
        await pilot.pause(delay=wait_s)
        # Drive a few more refreshes so the uptime strip shows several
        # cells; without this the strip renders as one solitary block.
        for _ in range(max(0, refreshes - 1)):
            await app.action_refresh()
            await pilot.pause(delay=wait_s)
        output.parent.mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(output))


def main() -> None:
    """Parse args and run the screenshot capture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("chap-checker.toml"),
        help="TOML config file (default: ./chap-checker.toml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/assets/dashboard.svg"),
        help="Output SVG path (default: docs/assets/dashboard.svg)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=20.0,
        help="Seconds to wait for each refresh to complete (default 20)",
    )
    parser.add_argument(
        "--refreshes",
        type=int,
        default=5,
        help="Number of refresh cycles to drive before capturing (default 5)",
    )
    args = parser.parse_args()
    if not args.config.exists():
        raise SystemExit(f"config file not found: {args.config}")
    asyncio.run(_capture(args.config, args.output, args.wait, args.refreshes))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
