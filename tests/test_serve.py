"""Smoke tests for the web dashboard.

The FastAPI app uses an asyncio background task that we do not want firing
real HTTP probes during unit tests. The tests below build the server with an
empty target list (or stubbed-in trackers) and inspect the snapshot model +
the static index route. End-to-end refresh-loop testing is deferred to
manual + integration coverage.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from pydantic import HttpUrl

from chap_checker.checks.base import CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.config import CheckerConfig, InstanceConfig
from chap_checker.daemon import DashboardServer, TileTracker
from chap_checker.runner import RunReport, TargetEntry
from chap_checker.serve import make_app


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
    tracker = TileTracker(ping_ok=3, ping_total=3, last_report=report)
    # Three prior refresh outcomes - one OK, one degraded, one failure
    # so all three sparkline colour paths get covered.
    tracker.history.extend([(Status.OK, 120), (Status.WARN, 140), (Status.FAIL, None)])
    server.trackers["prod"] = tracker
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
    assert [(p.status, p.latency_ms) for p in tile.history] == [
        (Status.OK, 120),
        (Status.WARN, 140),
        (Status.FAIL, None),
    ]


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
        # /api/state surfaces the [ui] config so the React client can render
        # the configured title + bootstrap the theme on first load.
        assert data["ui_title"] == "DHIS2 / Climate Instance Checker"
        assert data["ui_theme"] == "phosphor"


def test_state_endpoint_reflects_configured_ui() -> None:
    """A custom [ui] block in the config shows up on /api/state."""
    from chap_checker.config import UiConfig

    cfg = _cfg()
    cfg = cfg.model_copy(update={"ui": UiConfig(title="Operations", theme="amber")})
    server = DashboardServer(
        targets=[_target()],
        cfg=cfg,
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        data = client.get("/api/state").json()
        assert data["ui_title"] == "Operations"
        assert data["ui_theme"] == "amber"


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
        assert "DHIS2 / Climate Instance Checker" in r.text
        # The poller lives in _state.js; check it's served too.
        r2 = client.get("/_state.js")
        assert r2.status_code == 200
        assert "/api/state" in r2.text


# ---------- /api/reload ----------


_VALID_TOML = """\
[instances.alpha]
url = "https://alpha.example"
username = "u"
password = "p"
"""


_VALID_TOML_TWO = """\
[instances.alpha]
url = "https://alpha.example"
username = "u"
password = "p"

[instances.beta]
url = "https://beta.example"
username = "u"
password = "p"
"""


def _server_with_config(tmp_path: Path) -> tuple[DashboardServer, Path]:
    """Build a DashboardServer pointed at ``tmp_path/chap-checker.toml``."""
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML)
    return DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=cfg_path,
    ), cfg_path


def test_reload_endpoint_swaps_targets(tmp_path: Path) -> None:
    """Editing the toml + POST /api/reload swaps the in-memory targets."""
    server, _cfg_path = _server_with_config(tmp_path)
    # Server was built with the legacy _target() / _cfg(); first reload picks
    # up the on-disk toml and replaces the single "prod" with "alpha".
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/reload")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["instance_count"] == 1
        assert body["added"] == ["alpha"]
        assert body["removed"] == ["prod"]
        assert [t.name for t in server.targets] == ["alpha"]
        # /api/state immediately reflects the new instance set.
        snap = client.get("/api/state").json()
        assert snap["instance_count"] == 1
        assert snap["tiles"][0]["name"] == "alpha"


def test_reload_endpoint_adds_new_instance(tmp_path: Path) -> None:
    """Adding an instance to the toml and reloading appends it without dropping the first."""
    server, cfg_path = _server_with_config(tmp_path)
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/reload")  # first reload: prod -> alpha
        cfg_path.write_text(_VALID_TOML_TWO)
        r = client.post("/api/reload")
        assert r.status_code == 200
        body = r.json()
        assert body["instance_count"] == 2
        assert body["added"] == ["beta"]
        assert body["removed"] == []
        assert sorted(t.name for t in server.targets) == ["alpha", "beta"]


def test_reload_endpoint_returns_400_on_bad_toml(tmp_path: Path) -> None:
    """Malformed config returns 400 with the parse error and does not crash the server."""
    server, cfg_path = _server_with_config(tmp_path)
    cfg_path.write_text("this is = not [valid toml")
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/reload")
        assert r.status_code == 400
        body = r.json()
        assert body["status"] == "error"
        assert body["message"]  # non-empty error string
        # Server still serves /api/state — background loop survives.
        r2 = client.get("/api/state")
        assert r2.status_code == 200


def test_reload_endpoint_returns_404_when_config_missing(tmp_path: Path) -> None:
    """Deleting the config file between reloads surfaces as a 404."""
    server, cfg_path = _server_with_config(tmp_path)
    cfg_path.unlink()
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/reload")
        assert r.status_code == 404
        body = r.json()
        assert body["status"] == "error"


def test_reload_without_config_path_returns_400() -> None:
    """A server launched without config_path (ad-hoc verify mode) refuses to reload."""
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=None,
    )
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/reload")
        assert r.status_code == 400
        assert "config_path" in r.json()["message"]


# ---------- Bearer-token auth (0.7+) ----------


def _server_for_auth() -> DashboardServer:
    return DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )


def test_auth_disabled_regression_no_header_required() -> None:
    """Without `auth_token`, /api/state is still 200 with no Authorization header.

    Regression guard: the auth wiring must not break the unauthenticated
    default path that 0.6.x and earlier operators rely on.
    """
    app = make_app(_server_for_auth(), auth_token=None)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/state")
        assert r.status_code == 200, r.text


def test_auth_enabled_returns_401_without_header() -> None:
    app = make_app(_server_for_auth(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/state")
        assert r.status_code == 401
        assert r.headers.get("www-authenticate") == "Bearer"
        assert "missing bearer token" in r.text.lower()


def test_auth_enabled_returns_401_with_wrong_token() -> None:
    app = make_app(_server_for_auth(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/state", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        assert "bad token" in r.text.lower()


def test_auth_enabled_returns_200_with_correct_token() -> None:
    app = make_app(_server_for_auth(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/state", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200
        assert r.json()["instance_count"] == 1


def test_auth_enabled_reload_is_also_protected() -> None:
    """`/api/reload` is a state-mutating endpoint; must also require auth."""
    app = make_app(_server_for_auth(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/reload")
        assert r.status_code == 401


def test_auth_status_endpoint_reports_required_when_token_set() -> None:
    """The /api/auth probe is unprotected and reports auth + ui hints."""
    auth_on = make_app(_server_for_auth(), auth_token="s3cret")
    with TestClient(auth_on, raise_server_exceptions=False) as c:
        body = c.get("/api/auth").json()
        # `required` is the gate; `ui_theme` / `ui_title` let the login
        # modal honour the operator's [ui] config on first paint.
        assert body["required"] is True
        assert body["ui_theme"] == "phosphor"  # default
        assert body["ui_title"] == "DHIS2 / Climate Instance Checker"
    auth_off = make_app(_server_for_auth(), auth_token=None)
    with TestClient(auth_off, raise_server_exceptions=False) as c:
        assert c.get("/api/auth").json()["required"] is False


def test_auth_status_endpoint_surfaces_configured_theme() -> None:
    """`/api/auth` returns whatever theme the operator put under [ui]."""
    from chap_checker.config import UiConfig

    cfg = _cfg()
    cfg = cfg.model_copy(update={"ui": UiConfig(title="Operations", theme="dhis2")})
    server = DashboardServer(
        targets=[_target()],
        cfg=cfg,
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )
    app = make_app(server, auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/auth").json()
        assert body["ui_theme"] == "dhis2"
        assert body["ui_title"] == "Operations"


def test_auth_static_files_remain_public_for_login_modal() -> None:
    """The SPA shell + static assets must stay reachable so the login modal can render."""
    app = make_app(_server_for_auth(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        # GET / serves index.html with no Authorization header.
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # Static assets stay public too (the survival _state.js / _auth.js
        # are part of the shell).
        r2 = client.get("/_state.js")
        assert r2.status_code == 200


def _server_with_no_targets() -> DashboardServer:
    """Server with empty targets - refresh_once is a no-op (no network)."""
    return DashboardServer(
        targets=[],
        cfg=CheckerConfig(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
    )


def test_refresh_endpoint_runs_refresh_once() -> None:
    """POST /api/refresh drives a server-side run; the *Refresh now* command depends on it."""
    server = _server_with_no_targets()
    app = make_app(server, auth_token=None)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/refresh")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["instance_count"] == 0
        # last_refresh is populated by refresh_once even when there are no targets.
        assert body["last_refresh"] is not None


def test_refresh_endpoint_requires_auth_when_token_set() -> None:
    """The new /api/refresh endpoint is gated by the same bearer token as /api/state."""
    server = _server_with_no_targets()
    app = make_app(server, auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/api/refresh").status_code == 401
        ok = client.post("/api/refresh", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_reload_enables_auth_when_block_added(tmp_path: Path) -> None:
    """Adding an `[auth]` block to the TOML and reloading starts gating /api/*."""
    cfg_path = tmp_path / "chap-checker.toml"
    # Initial config has no [auth] - daemon is open.
    cfg_path.write_text(_VALID_TOML)
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=cfg_path,
    )
    app = make_app(server, auth_token=None)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/auth").json()["required"] is False

        # Operator edits the TOML to add [auth], reloads.
        cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken = "after-reload"\n')
        assert client.post("/api/reload").status_code == 200

        # /api/state now requires the new token.
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/state", headers={"Authorization": "Bearer after-reload"}).status_code == 200
        # /api/auth reports the change too.
        assert client.get("/api/auth").json()["required"] is True


def test_reload_disables_auth_when_block_removed(tmp_path: Path) -> None:
    """Removing the `[auth]` block via reload opens up /api/* immediately."""
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken = "initial-token"\n')
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=cfg_path,
    )
    app = make_app(server, auth_token="initial-token")
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/auth").json()["required"] is True

        # Operator removes [auth] from the TOML, reloads.
        cfg_path.write_text(_VALID_TOML)
        assert client.post("/api/reload", headers={"Authorization": "Bearer initial-token"}).status_code == 200

        # /api/state no longer requires the token.
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/auth").json()["required"] is False


def test_reload_rotates_token_when_block_changed(tmp_path: Path) -> None:
    """Changing the `[auth].token` value via reload makes the old token stop working."""
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken = "old-token"\n')
    server = DashboardServer(
        targets=[_target()],
        cfg=_cfg(),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=cfg_path,
    )
    app = make_app(server, auth_token="old-token")
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/state", headers={"Authorization": "Bearer old-token"}).status_code == 200

        cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken = "new-token"\n')
        assert client.post("/api/reload", headers={"Authorization": "Bearer old-token"}).status_code == 200

        assert client.get("/api/state", headers={"Authorization": "Bearer old-token"}).status_code == 401
        assert client.get("/api/state", headers={"Authorization": "Bearer new-token"}).status_code == 200
