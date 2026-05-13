"""Smoke tests for the web dashboard.

The FastAPI app uses an asyncio background task that we do not want firing
real HTTP probes during unit tests. The tests below build the server with an
empty target list (or stubbed-in trackers) and inspect the snapshot model +
the static index route. End-to-end refresh-loop testing is deferred to
manual + integration coverage.
"""

from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient
from pydantic import HttpUrl

from chap_checker.checks.base import CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.config import CheckerConfig, InstanceConfig
from chap_checker.runner import RunReport, TargetEntry
from chap_checker.web import DashboardServer, _TileTracker, make_app


def _cfg() -> CheckerConfig:
    return CheckerConfig(
        instances={
            "prod": InstanceConfig(
                url=cast(HttpUrl, "https://prod.example"),
                username="u",
                password="p",
            ),
        },
    )


def _target() -> TargetEntry:
    return TargetEntry(
        name="prod",
        target=Dhis2Target(
            base_url=cast(HttpUrl, "https://prod.example"),
            username="u",
            password="p",
        ),
    )


def test_snapshot_with_no_data_returns_skipped_tile() -> None:
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )
    snap = server.snapshot()
    assert snap.instance_count == 1
    assert snap.alerts_enabled is False
    assert len(snap.tiles) == 1
    tile = snap.tiles[0]
    assert tile.name == "prod"
    assert tile.worst_status is Status.SKIPPED
    assert tile.total_count == 0


def test_snapshot_reflects_tracker_state() -> None:
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=True,
    )
    # Seed the tracker directly to avoid hitting the network.
    report = RunReport(
        target_name="prod",
        target_url="https://prod.example",
        results=[
            CheckResult(name="dhis2_ping", status=Status.OK, message="", duration_ms=42.0),
            CheckResult(
                name="dhis2_system_info",
                status=Status.OK,
                message="",
                details={"version": "2.42.3"},
                duration_ms=58.0,
            ),
        ],
    )
    server.trackers["prod"] = _TileTracker(ping_ok=3, ping_total=3, last_report=report)
    snap = server.snapshot()
    tile = snap.tiles[0]
    assert tile.worst_status is Status.OK
    assert tile.ok_count == 2
    assert tile.total_count == 2
    assert tile.version == "2.42.3"
    assert tile.latency_ms == 50  # (42 + 58) / 2
    assert tile.uptime_pct == 100.0
    assert [c.name for c in tile.checks] == ["ping", "system_info"]
    assert [c.symbol for c in tile.checks] == ["✓", "✓"]


def test_state_endpoint_returns_json() -> None:
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )
    # Build the app without exercising the lifespan (no background task).
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        # The TestClient's context manager runs the lifespan, which spawns
        # the background task. That's fine - it sleeps for interval_s before
        # actually hitting the network.
        r = client.get("/api/state")
        assert r.status_code == 200
        data = r.json()
        assert data["instance_count"] == 1
        assert data["alerts_enabled"] is False
        assert data["interval_s"] == 30.0
        assert isinstance(data["tiles"], list)


def test_index_serves_html() -> None:
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "chap-checker" in r.text
        # Sanity-check the JS poller is present.
        assert "/api/state" in r.text
