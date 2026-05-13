"""Render check results to the console (Rich table) or to JSON (cron-friendly)."""

from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.table import Table

from chap_checker.checks.base import Status
from chap_checker.runner import RunReport, VerifyReport

_STATUS_STYLE = {
    Status.OK: "bold green",
    Status.WARN: "bold yellow",
    Status.FAIL: "bold red",
    Status.ERROR: "bold magenta",
    Status.SKIPPED: "dim",
}


def render(report: VerifyReport, *, json_output: bool) -> None:
    """Write ``report`` to stdout.

    Args:
        report: Verify-invocation result.
        json_output: If True, emit a single JSON document; otherwise render a
            Rich table per target.
    """
    if json_output:
        _render_json(report)
    else:
        _render_tables(report.runs)


def _render_json(report: VerifyReport) -> None:
    json.dump(report.model_dump(mode="json"), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _render_tables(reports: list[RunReport]) -> None:
    console = Console()
    # Pre-scan all reports so each per-target table uses the same column
    # widths. Without this, Rich sizes each table's columns to its own
    # contents and the verify output reads as a ladder of mismatched
    # widths whenever one instance has wider check names than another.
    check_w = max(
        (len(r.name) for report in reports for r in report.results),
        default=len("Check"),
    )
    status_w = max(len(s.value) for s in Status)
    duration_w = max(
        (len(f"{r.duration_ms:.0f} ms") for report in reports for r in report.results),
        default=len("Duration"),
    )
    for report in reports:
        title = f"chap-checker - {report.target_name} - {report.target_url}"
        table = Table(title=title)
        table.add_column("Check", style="cyan", no_wrap=True, min_width=check_w)
        table.add_column("Status", no_wrap=True, min_width=status_w)
        table.add_column("Duration", justify="right", style="dim", min_width=duration_w)
        table.add_column("Message", overflow="fold")
        for r in report.results:
            table.add_row(
                r.name,
                f"[{_STATUS_STYLE[r.status]}]{r.status.value.upper()}[/]",
                f"{r.duration_ms:.0f} ms",
                r.message,
            )
        console.print(table)
