"""Check protocol, result model and a tiny registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Protocol, TypeVar, cast, runtime_checkable

from dhis2w_client import Dhis2Client
from pydantic import BaseModel, Field


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

    name: ClassVar[str]
    description: ClassVar[str]
    order: ClassVar[int]
    requires: ClassVar[list[str]]

    async def run(self, client: Dhis2Client) -> CheckResult:
        """Execute the check against ``client`` and return a result."""
        ...


_REGISTRY: list[Check] = []


def register(check: Check) -> Check:
    """Register ``check`` so it shows up in :func:`all_checks`."""
    _REGISTRY.append(check)
    return check


_TCheck = TypeVar("_TCheck", bound=type[Check])


def register_check(cls: _TCheck) -> _TCheck:
    """Class decorator: instantiate ``cls`` and add the instance to the registry.

    Each check class is expected to take no constructor arguments. Use this
    in preference to a manual ``register(MyCheck())`` call at module bottom -
    it keeps registration co-located with the class declaration.

    Example:
        @register_check
        class Dhis2ChapPingCheck:
            name = "dhis2_chap_ping"
            ...
    """
    register(cast(Check, cls()))
    return cls


def all_checks() -> list[Check]:
    """Return all registered checks sorted by ``(order, name)``."""
    return sorted(_REGISTRY, key=lambda c: (c.order, c.name))


def resolve_checks(names: list[str] | None) -> list[Check]:
    """Resolve check ``names`` into :class:`Check` instances.

    Returns every registered check when ``names`` is ``None``. Otherwise
    returns the named checks plus the transitive closure of their
    ``requires``, so a partial selection always has its prerequisites
    available. Output is in the canonical ``(order, name)`` order.

    Raises ``KeyError`` if any name doesn't match a registered check.
    """
    if names is None:
        return all_checks()

    by_name = {c.name: c for c in all_checks()}
    selected: set[str] = set()

    def _add(name: str) -> None:
        if name in selected:
            return
        if name not in by_name:
            known = ", ".join(sorted(by_name))
            raise KeyError(f"unknown check '{name}'. Known: {known}")
        check = by_name[name]
        for req in check.requires:
            _add(req)
        selected.add(name)

    for n in names:
        _add(n)

    return [c for c in all_checks() if c.name in selected]
