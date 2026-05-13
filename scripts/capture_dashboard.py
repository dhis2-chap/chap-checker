"""Capture an SVG screenshot of the dashboard against a live config.

Used to regenerate ``docs/assets/dashboard.svg`` shown on the TUI docs page.
Requires real network access to the DHIS2 instances listed in the config.

Usage::

    uv run python scripts/capture_dashboard.py \
        --config chap-checker.toml \
        --output docs/assets/dashboard.svg \
        --wait 20
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from chap_checker.config import load_config
from chap_checker.dashboard import DashboardApp


async def _capture(config_path: Path, output: Path, wait_s: float) -> None:
    cfg = load_config(config_path)
    targets = [entry.to_target_entry(name) for name, entry in cfg.instances.items()]
    app = DashboardApp(
        targets=targets,
        cfg=cfg,
        config_path=config_path,
        state_path=None,
        # Disable auto-refresh during capture - we'll trigger one refresh manually
        # and then wait for it to land.
        interval_s=9999.0,
        alerts_enabled=False,
    )
    async with app.run_test(size=(180, 50)) as pilot:
        # The dashboard's on_mount kicks off one refresh via call_after_refresh.
        # Wait long enough for it to land against real DHIS2 instances.
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
        help="Seconds to wait for the first refresh to complete (default 20)",
    )
    args = parser.parse_args()
    if not args.config.exists():
        raise SystemExit(f"config file not found: {args.config}")
    asyncio.run(_capture(args.config, args.output, args.wait))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
