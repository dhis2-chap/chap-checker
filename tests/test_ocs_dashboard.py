"""Dashboard tiles for OCS instances: platform label, version source, ping/uptime source."""

from typing import Any

import pytest

from chap_checker.checks.base import CheckResult, Status
from chap_checker.config import CheckerConfig
from chap_checker.daemon import DashboardServer
from chap_checker.runner import RunReport, TargetEntry

_CFG = CheckerConfig.model_validate(
    {
        "instances": {
            "nepal": {"kind": "ocs", "url": "https://ocs.example", "name": "Nepal"},
            "play": {"url": "https://play.example", "username": "u", "password": "p"},
        }
    }
)


def _server() -> DashboardServer:
    targets = [inst.to_target_entry(name) for name, inst in _CFG.instances.items()]
    return DashboardServer(targets=targets, cfg=_CFG, state_path=None, interval_s=30.0, alerts_enabled=False)


def _ok(name: str, **details: Any) -> CheckResult:
    return CheckResult(name=name, status=Status.OK, message="", details=details, duration_ms=10.0)


def _reports(health: Status) -> list[RunReport]:
    return [
        RunReport(
            target_name="nepal",
            target_kind="ocs",
            target_url="https://ocs.example",
            results=[
                _ok("http_2xx"),
                CheckResult(name="ocs_health", status=health, message="", duration_ms=10.0),
                _ok("ocs_info", version="0.1.0")
                if health is Status.OK
                else CheckResult(name="ocs_info", status=Status.SKIPPED, message="Skipped"),
            ],
        ),
        RunReport(
            target_name="play",
            target_url="https://play.example",
            results=[_ok("dhis2_ping"), _ok("dhis2_system_info", version="2.42.3")],
        ),
    ]


async def _refresh(server: DashboardServer, monkeypatch: pytest.MonkeyPatch, health: Status) -> None:
    async def fake_run_targets(targets: list[TargetEntry], concurrency: int = 5) -> list[RunReport]:  # noqa: ARG001
        return _reports(health)

    monkeypatch.setattr("chap_checker.daemon.run_targets", fake_run_targets)
    await server.refresh_once()


@pytest.mark.asyncio
async def test_ocs_tile_uses_ocs_platform_version_and_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server()
    await _refresh(server, monkeypatch, Status.OK)
    await _refresh(server, monkeypatch, Status.FAIL)
    tiles = {t.name: t for t in server.snapshot().tiles}

    nepal = tiles["nepal"]
    assert nepal.platform == "OCS"
    assert nepal.display_name == "Nepal"
    assert (nepal.ping_ok, nepal.ping_total) == (1, 2)
    assert nepal.uptime_pct == 50.0
    # Last refresh had ocs_info SKIPPED, so no version is reported.
    assert nepal.version is None
    assert [c.name for c in nepal.checks] == ["http_2xx", "health", "info"]

    play = tiles["play"]
    assert play.platform == "DHIS2"
    assert play.version == "2.42.3"
    assert (play.ping_ok, play.ping_total) == (2, 2)


@pytest.mark.asyncio
async def test_ocs_tile_version_from_ocs_info(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server()
    await _refresh(server, monkeypatch, Status.OK)
    nepal = next(t for t in server.snapshot().tiles if t.name == "nepal")
    assert nepal.version == "0.1.0"
    assert nepal.uptime_pct == 100.0


def test_tile_before_first_refresh_has_platform() -> None:
    tiles = {t.name: t for t in _server().snapshot().tiles}
    assert tiles["nepal"].platform == "OCS"
    assert tiles["play"].platform == "DHIS2"
