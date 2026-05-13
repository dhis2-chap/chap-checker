"""Check protocol, result model and a tiny registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from chap_checker.client import Dhis2Client


class Status(StrEnum):
    """Outcome of a single check.

    ``SKIPPED`` is informational: it indicates the check did not execute
    because one of its declared prerequisites was not ``OK``. It is not
    included in the default alert ``notify_on`` and never produces a
    transition (see :func:`chap_checker.state_store.compute_transitions`).
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    """Structured result of a single check."""

    name: str
    status: Status
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


@runtime_checkable
class Check(Protocol):
    """Protocol all checks implement.

    ``order`` controls display / execution order: lower runs first, ties broken
    by ``name``. Built-in checks reserve multiples of 10 so new checks can be
    inserted without renumbering.

    ``requires`` lists other checks (by ``name``) whose result must be ``OK``
    before this one runs. If any prerequisite is not ``OK`` the runner skips
    this check and records :attr:`Status.SKIPPED`, suppressing cascade noise
    when a foundational check fails.
    """

    name: str
    description: str
    order: int
    requires: list[str]

    async def run(self, client: Dhis2Client) -> CheckResult:
        """Execute the check against ``client`` and return a result."""
        ...


_REGISTRY: list[Check] = []


def register(check: Check) -> Check:
    """Register ``check`` so it shows up in :func:`all_checks`."""
    _REGISTRY.append(check)
    return check


def all_checks() -> list[Check]:
    """Return all registered checks sorted by ``(order, name)``."""
    return sorted(_REGISTRY, key=lambda c: (c.order, c.name))
