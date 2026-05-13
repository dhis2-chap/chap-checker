"""Execute a set of checks against one or more targets."""

from __future__ import annotations

import asyncio
from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from chap_checker.checks.base import Check, CheckResult, Status, all_checks, resolve_checks
from chap_checker.client import Dhis2Client, Dhis2Target
from chap_checker.logging import get_logger

_log = get_logger("runner")


class TargetEntry(BaseModel):
    """A named target ready to be checked.

    ``check_names`` lets the caller restrict which checks run against this
    target. ``None`` means "every registered check". A non-empty list is
    resolved via :func:`chap_checker.checks.base.resolve_checks`, which
    also pulls in any transitive ``requires``.
    """

    name: str
    target: Dhis2Target
    check_names: list[str] | None = None


class TargetSummary(BaseModel):
    """Per-target counts of check results by status."""

    ok: int = 0
    warn: int = 0
    fail: int = 0
    error: int = 0
    skipped: int = 0


class RunReport(BaseModel):
    """Results for a single target."""

    target_name: str
    target_url: str
    results: list[CheckResult] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """True if every check came back ``OK``."""
        return all(r.status is Status.OK for r in self.results)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> TargetSummary:
        """Counts of results by status for this target."""
        counts: dict[str, int] = {"ok": 0, "warn": 0, "fail": 0, "error": 0, "skipped": 0}
        for r in self.results:
            counts[r.status.value] += 1
        return TargetSummary(**counts)


class VerifyReport(BaseModel):
    """Top-level report for a single ``chap-checker verify`` invocation."""

    checker_version: str
    started_at: datetime
    finished_at: datetime
    runs: list[RunReport]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """True if every target's checks all came back ``OK``."""
        return all(r.ok for r in self.runs)


async def run_checks(target: Dhis2Target, checks: list[Check] | None = None) -> list[CheckResult]:
    """Run ``checks`` (default: all registered) against ``target`` sequentially.

    A check whose ``requires`` includes any prior result that is not ``OK``
    is skipped (status :attr:`Status.SKIPPED`) without contacting the server.
    This stops the cascade where one failed foundational check produces N
    inevitable downstream failures.
    """
    selected = checks if checks is not None else all_checks()
    results: list[CheckResult] = []
    results_by_name: dict[str, CheckResult] = {}
    async with Dhis2Client(target) as client:
        for check in selected:
            failed_prereqs = [
                req
                for req in check.requires
                if req not in results_by_name or results_by_name[req].status is not Status.OK
            ]
            if failed_prereqs:
                result = CheckResult(
                    name=check.name,
                    status=Status.SKIPPED,
                    message=f"Skipped: {', '.join(failed_prereqs)} not OK.",
                )
            else:
                _log.debug("running check %s", check.name)
                try:
                    result = await check.run(client)
                except Exception as exc:  # noqa: BLE001 - never let one check break the run
                    _log.exception("check %s crashed", check.name)
                    result = CheckResult(name=check.name, status=Status.ERROR, message=f"Crashed: {exc}")
            results.append(result)
            results_by_name[check.name] = result
    return results


async def run_targets(targets: list[TargetEntry]) -> list[RunReport]:
    """Run each :class:`TargetEntry`'s checks sequentially.

    If ``entry.check_names`` is set, it picks the checks (and their transitive
    ``requires``); otherwise every registered check runs.
    """
    reports: list[RunReport] = []
    for entry in targets:
        _log.debug("running target %s", entry.name)
        checks = resolve_checks(entry.check_names)
        results = await run_checks(entry.target, checks)
        reports.append(
            RunReport(
                target_name=entry.name,
                target_url=str(entry.target.base_url),
                results=results,
            )
        )
    return reports


def run_targets_sync(targets: list[TargetEntry]) -> list[RunReport]:
    """Synchronous wrapper around :func:`run_targets`."""
    return asyncio.run(run_targets(targets))
