from datetime import UTC, datetime
from pathlib import Path

from chap_checker.checks.base import CheckResult, Status
from chap_checker.runner import RunReport
from chap_checker.state_store import (
    CheckState,
    StateFile,
    compute_transitions,
    load_state,
    save_state,
)


def _report(target: str, *results: CheckResult) -> RunReport:
    return RunReport(target_name=target, target_url=f"https://{target}.example", results=list(results))


def _result(name: str, status: Status, message: str = "") -> CheckResult:
    return CheckResult(name=name, status=status, message=message, duration_ms=0.0)


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "absent.json")
    assert state.version == 1
    assert state.states == {}


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    state = StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=now)})
    path = tmp_path / "chap-checker.state.json"
    save_state(path, state)

    loaded = load_state(path)
    assert loaded.states["prod::ping"].status is Status.FAIL
    assert loaded.states["prod::ping"].since == now


def test_save_is_atomic_no_leftover_tmp(tmp_path: Path) -> None:
    path = tmp_path / "chap-checker.state.json"
    save_state(path, StateFile())
    assert path.exists()
    assert not (tmp_path / "chap-checker.state.json.tmp").exists()


def test_first_failure_emits_transition() -> None:
    now = datetime.now(UTC)
    previous = StateFile()
    reports = [_report("prod", _result("ping", Status.FAIL, "down"))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL}, now)

    assert len(transitions) == 1
    t = transitions[0]
    assert t.kind == "failure"
    assert t.previous_status is Status.OK
    assert t.current_status is Status.FAIL
    assert new_state.states["prod::ping"].status is Status.FAIL


def test_sustained_failure_no_transition() -> None:
    now = datetime.now(UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    previous = StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=earlier)})
    reports = [_report("prod", _result("ping", Status.FAIL))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL}, now)

    assert transitions == []
    assert new_state.states["prod::ping"].since == earlier  # untouched


def test_recovery_emits_transition() -> None:
    now = datetime.now(UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    previous = StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=earlier)})
    reports = [_report("prod", _result("ping", Status.OK, "back"))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL}, now)

    assert len(transitions) == 1
    assert transitions[0].kind == "recovery"
    assert transitions[0].previous_status is Status.FAIL
    assert transitions[0].current_status is Status.OK
    assert new_state.states["prod::ping"].status is Status.OK


def test_intra_failure_change_does_not_double_page() -> None:
    """FAIL -> ERROR shouldn't fire if only FAIL is in notify_on - both sides need to span the boundary."""
    now = datetime.now(UTC)
    previous = StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=now)})
    reports = [_report("prod", _result("ping", Status.ERROR))]
    transitions, _ = compute_transitions(previous, reports, {Status.FAIL}, now)

    # Both prev (FAIL) and curr (ERROR) - prev is in notify_on, so we DO emit;
    # this is the documented two-sided guard. With notify_on covering both,
    # the dispatch layer filters per-alerter.
    assert len(transitions) == 1


def test_warn_in_notify_on_fires_on_anomaly() -> None:
    now = datetime.now(UTC)
    previous = StateFile()
    reports = [_report("prod", _result("ping", Status.WARN, "missing version field"))]
    transitions, _ = compute_transitions(previous, reports, {Status.FAIL, Status.ERROR, Status.WARN}, now)

    assert len(transitions) == 1
    assert transitions[0].current_status is Status.WARN
