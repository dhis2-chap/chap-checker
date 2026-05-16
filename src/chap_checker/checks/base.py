"""Check protocol, result model and a tiny registry."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar, Protocol, TypeVar, cast, runtime_checkable

from dhis2w_client import Dhis2, Dhis2Client
from pydantic import BaseModel, ConfigDict, Field

from chap_checker.client import Dhis2Target

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)")


def parse_dhis2_version(version_str: str) -> Dhis2 | None:
    """Map a DHIS2 `/api/system/info` `version` field to a `Dhis2` enum.

    DHIS2 reports its version as e.g. `"2.42.3"` or `"2.44-SNAPSHOT"`. We
    pull the minor number and map it to `Dhis2.V41`, `Dhis2.V42`, or
    `Dhis2.V43` (the versions dhis2w-client 0.14 ships generated modules
    for). Unrecognised or out-of-range versions return None so the caller
    can skip version-typed work without crashing the run.

    Mirrors dhis2w-client's internal `_pick_version_key` semantics without
    relying on a private API.
    """
    match = _VERSION_RE.match(version_str or "")
    if not match:
        return None
    key = f"v{int(match.group(2))}"
    try:
        return Dhis2(key)
    except ValueError:
        return None


def format_request_error(exc: BaseException, *, path: str | None = None) -> str:
    """Render a transport exception into a useful operator-facing message.

    `str(httpx.TimeoutException)` is often empty - "Request failed: " on its
    own tells the operator nothing. This helper always carries the exception
    type and falls back to "(no message)" when `str(exc)` is empty. Pass the
    request `path` when known so an operator can tell which endpoint failed
    without grepping the check source.

    Example outputs:

        ReadTimeout (/api/me): (no message)
        ConnectError (/api/system/info): [Errno 8] nodename nor servname provided
    """
    base = str(exc).strip() or "(no message)"
    where = f" ({path})" if path else ""
    return f"{type(exc).__name__}{where}: {base}"


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


class CheckContext(BaseModel):
    """Per-target run context handed to every `Check.run` call.

    Built incrementally by the runner: starts empty for the first check
    and accumulates as checks complete. A check can read
    `ctx.dhis2_version` once `dhis2_system_info` has run to decide which
    typed resources or version-specific payload parsers to use; the
    field is `None` whenever the probe hasn't happened yet, failed, or
    reported an unrecognised version string.

    `prior_results` is a name->result dict of every check that has
    completed (in any status) before this one. The runner mutates the
    context in place between checks, so a check should treat its
    contents as a snapshot at the moment `run()` was called.

    Custom checks that don't need any of this can ignore the `ctx`
    argument entirely - it's there for the checks that do.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    target: Dhis2Target
    dhis2_version: Dhis2 | None = None
    prior_results: dict[str, CheckResult] = Field(default_factory=dict)


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

    The runner passes both an opened ``Dhis2Client`` and a
    :class:`CheckContext`. Most checks only need ``client``; the context
    is there for checks that want to:

    - read the detected DHIS2 version (`ctx.dhis2_version`) to pick a
      version-typed payload parser from `dhis2w_client.generated.v4*`,
    - peek at an earlier check's `details` via `ctx.prior_results[name]`,
    - open their own typed client when needed via
      `ctx.target.open(version=ctx.dhis2_version)`.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    order: ClassVar[int]
    requires: ClassVar[list[str]]

    async def run(self, client: Dhis2Client, ctx: CheckContext) -> CheckResult:
        """Execute the check against `client` and return a result.

        `ctx` is mutable shared state for the target's run. Reading
        `ctx.dhis2_version` / `ctx.prior_results` is the common case;
        writing fields (e.g. populating `ctx.dhis2_version` from
        `/api/system/info`) is a deliberate signal to later checks.
        """
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
