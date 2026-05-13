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
    # Check / Status / Duration are effectively fixed-size: check names
    # are short identifiers (longest registered is `dhis2_chap_modeling_app`
    # at 23 chars), Status is a five-value enum (longest `SKIPPED`), and
    # Duration tops out around `9999 ms`. Locking them at sensible widths
    # is simpler than pre-scanning the reports and produces identical
    # column layout across all per-target tables.
    for report in reports:
        title = f"chap-checker - {report.target_name} - {report.target_url}"
        # `expand=True` makes the table fill the terminal; without
        # `ratio=1` on Message, Rich distributes the extra width across
        # every column proportionally and the fixed `width=` values get
        # ignored on wider terminals. Pinning the ratio to Message means
        # any surplus width feeds the dynamic column instead.
        table = Table(title=title, expand=True)
        table.add_column("Check", style="cyan", no_wrap=True, width=25)
        table.add_column("Status", no_wrap=True, width=7)
        table.add_column("Duration", justify="right", style="dim", width=8)
        table.add_column("Message", overflow="fold", ratio=1)
        for r in report.results:
            table.add_row(
                r.name,
                f"[{_STATUS_STYLE[r.status]}]{r.status.value.upper()}[/]",
                f"{r.duration_ms:.0f} ms",
                r.message,
            )
        console.print(table)
