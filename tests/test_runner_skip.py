from typing import Any, cast

import pytest
from dhis2w_client import Dhis2Client

from chap_checker.checks.base import Check, CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.runner import run_checks


class _FakeCheck:
    """Test fake. The Check protocol declares its attrs as ``ClassVar`` but
    tests need per-instance customization, so we set them in ``__init__`` and
    cast through :class:`Check` at use sites via :func:`_as_check`."""

    def __init__(self, name: str, status: Status, requires: list[str] | None = None) -> None:
        self.name = name
        self.description = f"{name} (test)"
        self.order = 10
        self.requires: list[str] = requires or []
        self._status = status
        self.calls = 0

    async def run(self, client: Dhis2Client) -> CheckResult:
        self.calls += 1
        return CheckResult(name=self.name, status=self._status, message="ran", duration_ms=1.0)


def _as_check(fake: _FakeCheck) -> Check:
    return cast(Check, fake)


def _target() -> Dhis2Target:
    return Dhis2Target(
        base_url="https://nope.example",  # type: ignore[arg-type]
        username="u",
        password="p",
    )


def _stub_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the upstream Dhis2Client's context-manager protocol to no-ops.

    Tests in this module exercise `run_checks`'s skip / cascade logic with
    fake checks — they don't need a real HTTP pool. The stub keeps
    `target.open()` cheap and side-effect-free.
    """

    async def _fake_aenter(self: Any) -> Any:
        return self

    async def _fake_aexit(self: Any, *args: Any) -> None:
        return None

    monkeypatch.setattr(Dhis2Client, "__aenter__", _fake_aenter)
    monkeypatch.setattr(Dhis2Client, "__aexit__", _fake_aexit)


@pytest.mark.asyncio
async def test_dependent_check_skipped_when_prereq_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = _FakeCheck("ping", Status.FAIL)
    dependent = _FakeCheck("system-info", Status.OK, requires=["ping"])

    _stub_upstream(monkeypatch)

    results = await run_checks(_target(), checks=[_as_check(failing), _as_check(dependent)])

    assert [r.status for r in results] == [Status.FAIL, Status.SKIPPED]
    assert dependent.calls == 0
    assert "ping" in results[1].message


@pytest.mark.asyncio
async def test_dependent_check_runs_when_prereq_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    foundational = _FakeCheck("ping", Status.OK)
    dependent = _FakeCheck("system-info", Status.OK, requires=["ping"])

    _stub_upstream(monkeypatch)

    results = await run_checks(_target(), checks=[_as_check(foundational), _as_check(dependent)])

    assert [r.status for r in results] == [Status.OK, Status.OK]
    assert foundational.calls == 1
    assert dependent.calls == 1


@pytest.mark.asyncio
async def test_unrelated_check_runs_even_when_sibling_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    ping = _FakeCheck("ping", Status.OK)
    failing = _FakeCheck("chap-route", Status.FAIL, requires=["ping"])
    independent = _FakeCheck("modeling-app", Status.OK, requires=["ping"])

    _stub_upstream(monkeypatch)

    results = await run_checks(_target(), checks=[_as_check(ping), _as_check(failing), _as_check(independent)])
    by_name = {r.name: r for r in results}

    assert by_name["chap-route"].status is Status.FAIL
    assert by_name["modeling-app"].status is Status.OK
    assert independent.calls == 1
