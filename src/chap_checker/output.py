"""Render check results to the console (Rich table) or to JSON (cron-friendly)."""

from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.table import Table

from chap_checker.checks.base import Status
from chap_checker.runner import RunReport

_STATUS_STYLE = {
    Status.OK: "bold green",
    Status.WARN: "bold yellow",
    Status.FAIL: "bold red",
    Status.ERROR: "bold magenta",
}


def render(reports: list[RunReport], *, json_output: bool) -> None:
    """Write ``reports`` to stdout.

    Args:
        reports: One :class:`RunReport` per target.
        json_output: If True, emit a single JSON document; otherwise render a
            Rich table per target.
    """
    if json_output:
        _render_json(reports)
    else:
        _render_tables(reports)


def _render_json(reports: list[RunReport]) -> None:
    payload = {
        "ok": all(r.ok for r in reports),
        "runs": [
            {
                "target_name": r.target_name,
                "target_url": r.target_url,
                "ok": r.ok,
                "results": [c.model_dump(mode="json") for c in r.results],
            }
            for r in reports
        ],
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _render_tables(reports: list[RunReport]) -> None:
    console = Console()
    for report in reports:
        title = f"chap-checker - {report.target_name} - {report.target_url}"
        table = Table(title=title)
        table.add_column("Check", style="cyan", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Duration", justify="right", style="dim")
        table.add_column("Message", overflow="fold")
        for r in report.results:
            table.add_row(
                r.name,
                f"[{_STATUS_STYLE[r.status]}]{r.status.value.upper()}[/]",
                f"{r.duration_ms:.0f} ms",
                r.message,
            )
        console.print(table)
