"""Refresh serialization, reload coordination and timestamps in `DashboardServer`.

Every test stubs `chap_checker.daemon.run_targets`, so `refresh_once()` runs
for real against canned reports and never touches the network. Slow runs are
modelled with an `asyncio.Event` the test releases by hand.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from chap_checker.checks.base import CheckResult, Status
from chap_checker.config import load_config
from chap_checker.daemon import DashboardServer
from chap_checker.runner import RunReport, TargetEntry

_PROD_TOML = """\
[instances.prod]
url = "https://prod.example"
username = "u"
password = "p"
"""

_PROD_MOVED_TOML = """\
[instances.prod]
url = "https://prod-new.example"
username = "u"
password = "p"
"""

_PROD_RELABELLED_TOML = """\
[instances.prod]
name = "Production"
url = "https://prod.example"
username = "u"
password = "other-password"
"""


def _server(tmp_path: Path, toml: str = _PROD_TOML) -> DashboardServer:
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(toml)
    cfg = load_config(cfg_path)
    targets = [inst.to_target_entry(name) for name, inst in cfg.instances.items()]
    return DashboardServer(
        targets=targets,
        cfg=cfg,
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=cfg_path,
    )


def _report(name: str, status: Status, url: str = "https://prod.example") -> RunReport:
    return RunReport(
        target_name=name,
        target_url=url,
        results=[CheckResult(name="dhis2_ping", status=status, message=status.value, duration_ms=5.0)],
    )


class _ScriptedRunTargets:
    """Stand-in for `run_targets` that replays a script of (status, gate) steps.

    Each call pops the next step, optionally waits on its gate (so the test
    controls when a "slow" run finishes) and returns one report per target
    with that status. It also records how many calls overlap.
    """

    def __init__(self, steps: list[tuple[Status, asyncio.Event | None]]) -> None:
        self.steps = list(steps)
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.started = asyncio.Event()

    async def __call__(self, targets: list[TargetEntry], concurrency: int = 5) -> list[RunReport]:  # noqa: ARG002
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.started.set()
        status, gate = self.steps.pop(0)
        try:
            if gate is not None:
                await gate.wait()
            return [_report(t.name, status, str(t.target.base_url)) for t in targets]
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_overlapping_refreshes_join_instead_of_running_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh requested while one is in flight joins it rather than racing it."""
    server = _server(tmp_path)
    gate = asyncio.Event()
    fake = _ScriptedRunTargets([(Status.OK, gate), (Status.FAIL, None)])
    monkeypatch.setattr("chap_checker.daemon.run_targets", fake)

    first = asyncio.create_task(server.refresh_once())
    await fake.started.wait()
    # Several manual refreshes pile up behind the slow background run.
    joiners = [asyncio.create_task(server.refresh_once()) for _ in range(3)]
    await asyncio.sleep(0)
    assert not any(j.done() for j in joiners), "joiners must wait for the in-flight run"
    gate.set()
    await asyncio.gather(first, *joiners)

    # One network pass served all four callers, never two at once.
    assert fake.calls == 1
    assert fake.max_in_flight == 1
    assert server.trackers["prod"].history[-1][0] is Status.OK

    # The next refresh runs normally and its newer FAIL is what sticks.
    await server.refresh_once()
    assert fake.calls == 2
    tracker = server.trackers["prod"]
    assert tracker.last_report is not None
    assert tracker.last_report.results[0].status is Status.FAIL
    assert [s for s, _ in tracker.history] == [Status.OK, Status.FAIL]


@pytest.mark.asyncio
async def test_slow_older_refresh_cannot_overwrite_newer_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow OK run never lands after a newer FAIL run: runs are strictly ordered."""
    server = _server(tmp_path)
    slow_gate = asyncio.Event()
    fake = _ScriptedRunTargets([(Status.OK, slow_gate), (Status.FAIL, None)])
    monkeypatch.setattr("chap_checker.daemon.run_targets", fake)

    slow = asyncio.create_task(server.refresh_once())
    await fake.started.wait()
    # Release the slow run only after the second caller is already waiting.
    second = asyncio.create_task(server.refresh_once())
    await asyncio.sleep(0)
    slow_gate.set()
    await asyncio.gather(slow, second)
    await server.refresh_once()

    tracker = server.trackers["prod"]
    assert fake.max_in_flight == 1
    assert tracker.last_report is not None
    assert tracker.last_report.results[0].status is Status.FAIL


@pytest.mark.asyncio
async def test_reload_during_refresh_discards_stale_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Results from a run started before reload() are not applied to the new config."""
    server = _server(tmp_path)
    gate = asyncio.Event()
    fake = _ScriptedRunTargets([(Status.OK, gate), (Status.FAIL, None)])
    monkeypatch.setattr("chap_checker.daemon.run_targets", fake)

    in_flight = asyncio.create_task(server.refresh_once())
    await fake.started.wait()
    # Operator points `prod` at a new server while the old one is being checked.
    assert server.config_path is not None
    server.config_path.write_text(_PROD_MOVED_TOML)
    server.reload()
    gate.set()
    await in_flight

    tracker = server.trackers["prod"]
    assert tracker.last_report is None, "old server's result leaked onto the reloaded target"
    assert tracker.ping_total == 0
    assert list(tracker.history) == []
    assert server.last_refresh is None
    tile = server.snapshot().tiles[0]
    assert tile.url == "https://prod-new.example"
    assert tile.worst_status is Status.SKIPPED

    # The next cycle runs against the new config and is applied.
    await server.refresh_once()
    tracker = server.trackers["prod"]
    assert tracker.last_report is not None
    assert tracker.last_report.target_url.startswith("https://prod-new.example")


@pytest.mark.asyncio
async def test_in_place_config_swap_during_refresh_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assigning new `targets` directly (the TUI's local reload) also invalidates the in-flight run."""
    server = _server(tmp_path)
    gate = asyncio.Event()
    fake = _ScriptedRunTargets([(Status.OK, gate)])
    monkeypatch.setattr("chap_checker.daemon.run_targets", fake)

    in_flight = asyncio.create_task(server.refresh_once())
    await fake.started.wait()
    server.targets = list(server.targets)
    gate.set()
    await in_flight
    assert server.trackers["prod"].last_report is None


@pytest.mark.asyncio
async def test_url_change_on_reload_resets_tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pointing an instance at a new URL starts its counters and uptime strip over."""
    server = _server(tmp_path)
    fake = _ScriptedRunTargets([(Status.OK, None), (Status.FAIL, None)])
    monkeypatch.setattr("chap_checker.daemon.run_targets", fake)
    await server.refresh_once()
    await server.refresh_once()
    assert server.trackers["prod"].ping_total == 2

    assert server.config_path is not None
    server.config_path.write_text(_PROD_MOVED_TOML)
    added, removed = server.reload()
    assert (added, removed) == (set(), set())

    tracker = server.trackers["prod"]
    assert tracker.ping_total == 0
    assert tracker.last_report is None
    assert list(tracker.history) == []
    assert server.snapshot().tiles[0].history == []


@pytest.mark.asyncio
async def test_non_identity_change_on_reload_keeps_tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relabelling or rotating credentials keeps the same server, so history survives."""
    server = _server(tmp_path)
    fake = _ScriptedRunTargets([(Status.OK, None)])
    monkeypatch.setattr("chap_checker.daemon.run_targets", fake)
    await server.refresh_once()

    assert server.config_path is not None
    server.config_path.write_text(_PROD_RELABELLED_TOML)
    server.reload()

    tracker = server.trackers["prod"]
    assert tracker.ping_total == 1
    assert len(tracker.history) == 1


@pytest.mark.asyncio
async def test_snapshot_timestamps_are_utc_aware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot times are timezone-aware UTC and serialize with an offset."""
    server = _server(tmp_path)
    monkeypatch.setattr("chap_checker.daemon.run_targets", _ScriptedRunTargets([(Status.OK, None)]))
    await server.refresh_once()

    snap = server.snapshot()
    assert snap.last_refresh is not None
    assert snap.last_refresh.utcoffset() == timedelta(0)
    tile_stamp = snap.tiles[0].last_refresh
    assert tile_stamp is not None
    assert tile_stamp.utcoffset() == timedelta(0)

    data = snap.model_dump(mode="json")
    for raw in (data["last_refresh"], data["tiles"][0]["last_refresh"]):
        assert raw.endswith("Z") or raw.endswith("+00:00"), raw
