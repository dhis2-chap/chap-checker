"""Execute a set of checks against one or more targets."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from chap_checker.checks.base import Check, CheckResult, Status, all_checks
from chap_checker.client import Dhis2Client, Dhis2Target
from chap_checker.logging import get_logger

_log = get_logger("runner")


class TargetEntry(BaseModel):
    """A named target ready to be checked."""

    name: str
    target: Dhis2Target


class RunReport(BaseModel):
    """Results for a single target."""

    target_name: str
    target_url: str
    results: list[CheckResult] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if every check came back ``OK``."""
        return all(r.status is Status.OK for r in self.results)


async def run_checks(target: Dhis2Target, checks: list[Check] | None = None) -> list[CheckResult]:
    """Run ``checks`` (default: all registered) against ``target`` sequentially.

    Sequential execution keeps DHIS2 load low and makes log output readable;
    we can revisit concurrency once we have many checks.
    """
    selected = checks if checks is not None else all_checks()
    results: list[CheckResult] = []
    async with Dhis2Client(target) as client:
        for check in selected:
            _log.debug("running check %s", check.name)
            try:
                result = await check.run(client)
            except Exception as exc:  # noqa: BLE001 - never let one check break the run
                _log.exception("check %s crashed", check.name)
                result = CheckResult(name=check.name, status=Status.ERROR, message=f"Crashed: {exc}")
            results.append(result)
    return results


async def run_targets(targets: list[TargetEntry]) -> list[RunReport]:
    """Run all registered checks against each :class:`TargetEntry` sequentially."""
    reports: list[RunReport] = []
    for entry in targets:
        _log.debug("running target %s", entry.name)
        results = await run_checks(entry.target)
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
