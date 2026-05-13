"""Tests for parallel target execution and the concurrency knob."""

import asyncio
from typing import Any, cast

import pytest
from pydantic import HttpUrl
from typer.testing import CliRunner

from chap_checker.checks.base import CheckResult, Status
from chap_checker.cli import app
from chap_checker.client import Dhis2Target
from chap_checker.config import DEFAULT_CONCURRENCY, CheckerConfig, InstanceConfig
from chap_checker.runner import TargetEntry, run_targets


def _target(name: str) -> TargetEntry:
    return TargetEntry(
        name=name,
        target=Dhis2Target(
            base_url=cast(HttpUrl, f"https://{name}.example"),
            username="u",
            password="p",
        ),
    )


@pytest.mark.asyncio
async def test_run_targets_preserves_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.gather returns in input order; the report order must match."""
    targets = [_target("a"), _target("b"), _target("c"), _target("d")]

    async def _fake_run_checks(target: Any, checks: Any) -> list[CheckResult]:
        return [CheckResult(name="x", status=Status.OK, message="", duration_ms=0.0)]

    monkeypatch.setattr("chap_checker.runner.run_checks", _fake_run_checks)

    reports = await run_targets(targets, concurrency=2)
    assert [r.target_name for r in reports] == ["a", "b", "c", "d"]


@pytest.mark.asyncio
async def test_run_targets_is_actually_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    """With concurrency=4 and 4 sleeping checks of 0.05s each, total wall time
    should be ~0.05s (parallel), not ~0.2s (sequential)."""
    targets = [_target(f"t{i}") for i in range(4)]
    sleep_s = 0.05

    async def _slow_run_checks(target: Any, checks: Any) -> list[CheckResult]:
        await asyncio.sleep(sleep_s)
        return [CheckResult(name="x", status=Status.OK, message="", duration_ms=0.0)]

    monkeypatch.setattr("chap_checker.runner.run_checks", _slow_run_checks)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await run_targets(targets, concurrency=4)
    elapsed = loop.time() - start

    # Parallel: ~sleep_s. Sequential would be ~4 * sleep_s. Generous upper
    # bound to avoid CI flakes.
    assert elapsed < sleep_s * 3, f"expected ~{sleep_s}s, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_run_targets_concurrency_one_is_sequential(monkeypatch: pytest.MonkeyPatch) -> None:
    """concurrency=1 falls back to sequential - total time is the sum."""
    targets = [_target(f"t{i}") for i in range(3)]
    sleep_s = 0.03

    async def _slow_run_checks(target: Any, checks: Any) -> list[CheckResult]:
        await asyncio.sleep(sleep_s)
        return [CheckResult(name="x", status=Status.OK, message="", duration_ms=0.0)]

    monkeypatch.setattr("chap_checker.runner.run_checks", _slow_run_checks)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await run_targets(targets, concurrency=1)
    elapsed = loop.time() - start

    assert elapsed >= sleep_s * 3 * 0.9


def test_concurrency_must_be_positive_in_config() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        CheckerConfig(
            instances={
                "x": InstanceConfig(
                    url=cast(HttpUrl, "https://x.test"),
                    username="u",
                    password="p",
                )
            },
            concurrency=0,
        )


def test_concurrency_upper_bound_in_config() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        CheckerConfig(
            instances={
                "x": InstanceConfig(
                    url=cast(HttpUrl, "https://x.test"),
                    username="u",
                    password="p",
                )
            },
            concurrency=999,
        )


def test_default_concurrency_is_five() -> None:
    cfg = CheckerConfig()
    assert cfg.concurrency == DEFAULT_CONCURRENCY == 5


def test_verify_declares_concurrency_flag() -> None:
    """Inspect the Click command tree directly - more robust than asserting on
    typer's Rich-rendered help output, which gets clipped in headless CI."""
    from typer.main import get_command

    click_app = get_command(app)
    verify = click_app.commands["verify"]  # type: ignore[attr-defined]
    flags: set[str] = set()
    for param in verify.params:
        flags.update(getattr(param, "opts", []))
    assert "--concurrency" in flags


def test_cli_rejects_zero_concurrency() -> None:
    """Typer min=1 should reject 0 at the CLI layer with exit code 2."""
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--url",
            "https://x.example",
            "-u",
            "u",
            "-p",
            "p",
            "--concurrency",
            "0",
        ],
    )
    assert result.exit_code == 2
