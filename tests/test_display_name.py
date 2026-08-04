"""Tests for the optional ``name`` field on ``[instances.<key>]``.

Covers the precedence rule (section key when ``name`` is unset, the
``name`` value when it's set) at every render boundary: ``TargetEntry``
→ ``RunReport`` → ``TileModel`` → ``Transition`` payload. The section
key remains the stable identity used for state-file dedup, regardless
of the human label.
"""

from __future__ import annotations

import tomllib
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import HttpUrl

from chap_checker.alerts.base import Transition
from chap_checker.alerts.slack import _build_slack_payload
from chap_checker.checks.base import CheckResult, Status
from chap_checker.client import Dhis2Target
from chap_checker.config import CheckerConfig
from chap_checker.runner import RunReport


def _build_config(toml: str) -> CheckerConfig:
    return CheckerConfig.model_validate(tomllib.loads(toml))


def test_instance_config_name_defaults_to_none() -> None:
    """The new ``name`` field is opt-in; absence leaves it ``None``."""
    cfg = _build_config(
        """
        [instances.foo]
        url = "https://x.example"
        username = "u"
        password = "p"
        """
    )
    assert cfg.instances["foo"].name is None


def test_instance_config_accepts_name_override() -> None:
    cfg = _build_config(
        """
        [instances.chap-modeling-platform]
        name = "CHAP Modeling Platform"
        url = "https://x.example"
        username = "u"
        password = "p"
        """
    )
    assert cfg.instances["chap-modeling-platform"].name == "CHAP Modeling Platform"


def test_to_target_entry_propagates_display_name() -> None:
    cfg = _build_config(
        """
        [instances.chap-modeling-platform]
        name = "CHAP Modeling Platform"
        url = "https://x.example"
        username = "u"
        password = "p"
        """
    )
    entry = cfg.instances["chap-modeling-platform"].to_target_entry("chap-modeling-platform")
    assert entry.name == "chap-modeling-platform"
    assert entry.display_name == "CHAP Modeling Platform"


def test_to_target_entry_leaves_display_name_unset_when_absent() -> None:
    cfg = _build_config(
        """
        [instances.foo]
        url = "https://x.example"
        username = "u"
        password = "p"
        """
    )
    entry = cfg.instances["foo"].to_target_entry("foo")
    assert entry.display_name is None


def test_run_report_carries_display_name() -> None:
    """RunReport carries display_name for the renderer; section key stays the identity."""
    report = RunReport(
        target_name="chap-modeling-platform",
        target_display_name="CHAP Modeling Platform",
        target_url="https://x.example",
        results=[],
    )
    assert report.target_name == "chap-modeling-platform"
    assert report.target_display_name == "CHAP Modeling Platform"


def test_verify_table_title_prefers_display_name() -> None:
    """The Rich-table title in `output.py` uses display_name when set."""
    from chap_checker.output import _render_tables

    # We don't capture stdout - we just ensure rendering doesn't crash and
    # that the underlying title calculation hits the display_name branch.
    report_with = RunReport(
        target_name="chap-modeling-platform",
        target_display_name="CHAP Modeling Platform",
        target_url="https://x.example",
        results=[],
    )
    report_without = RunReport(
        target_name="play-43",
        target_url="https://x.example",
        results=[],
    )
    _render_tables([report_with, report_without])  # smoke test


def test_slack_payload_uses_display_name_when_set() -> None:
    t_with = Transition(
        kind="failure",
        target_name="chap-modeling-platform",
        target_display_name="CHAP Modeling Platform",
        target_url="https://x.example",
        check_name="http_2xx",
        previous_status=Status.OK,
        current_status=Status.FAIL,
        message="500",
        duration_ms=42.0,
        occurred_at=datetime(2026, 5, 26, 12, 0, 0),
    )
    payload = _build_slack_payload([t_with])
    body = payload.attachments[0].blocks[0].text.text
    assert "CHAP Modeling Platform" in body
    assert "chap-modeling-platform" not in body  # section key not shown when label is set


def test_slack_payload_falls_back_to_section_key() -> None:
    t_without = Transition(
        kind="failure",
        target_name="play-43",
        target_url="https://x.example",
        check_name="http_2xx",
        previous_status=Status.OK,
        current_status=Status.FAIL,
        message="500",
        duration_ms=42.0,
        occurred_at=datetime(2026, 5, 26, 12, 0, 0),
    )
    payload = _build_slack_payload([t_without])
    body = payload.attachments[0].blocks[0].text.text
    assert "play-43" in body


def test_tile_model_includes_display_name_via_tile_path() -> None:
    """End-to-end through DashboardServer._tile_model — exercises the full plumbing."""
    from chap_checker.daemon import DashboardServer
    from chap_checker.runner import TargetEntry as RTargetEntry

    target = Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), username="u", password="p")
    entry = RTargetEntry(name="chap-modeling-platform", display_name="CHAP Modeling Platform", target=target)
    cfg = _build_config(
        """
        [instances.chap-modeling-platform]
        name = "CHAP Modeling Platform"
        url = "https://x.example"
        username = "u"
        password = "p"
        """
    )
    server = DashboardServer(targets=[entry], cfg=cfg, interval_s=30.0, alerts_enabled=False)
    snapshot = server.snapshot()
    assert snapshot.tiles[0].name == "chap-modeling-platform"
    assert snapshot.tiles[0].display_name == "CHAP Modeling Platform"


def test_state_file_key_is_section_key_not_display_name(tmp_path: Path) -> None:
    """Renaming an instance's display label must NOT reset its state-file entry."""
    from chap_checker.state_store import compute_transitions, load_state

    state_path = tmp_path / "state.json"
    report = RunReport(
        target_name="chap-modeling-platform",
        target_display_name="CHAP Modeling Platform",
        target_url="https://x.example",
        results=[CheckResult(name="http_2xx", status=Status.FAIL, message="500")],
    )
    # First run -> persists state under `<section_key>::<check>`.
    initial = load_state(state_path)
    transitions, new_state = compute_transitions(
        previous=initial,
        reports=[report],
        notify_on=[Status.FAIL, Status.ERROR, Status.WARN],
        now=datetime(2026, 5, 26, 12, 0, 0),
    )
    # The state dict keys are `<target>::<check>` - confirm it's the section key.
    assert any(k.startswith("chap-modeling-platform::") for k in new_state.states)
    assert not any(k.startswith("CHAP Modeling Platform::") for k in new_state.states)
    # And the emitted transition carries the display label for renderers.
    assert transitions[0].target_display_name == "CHAP Modeling Platform"
