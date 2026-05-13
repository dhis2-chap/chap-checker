import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
    # No *.tmp leftovers - the unique tempfile name is renamed atomically.
    leftovers = list(tmp_path.glob("chap-checker.state.json.*.tmp"))
    assert leftovers == []


def test_concurrent_saves_use_unique_tmp_files(tmp_path: Path) -> None:
    """Two save_state calls must not collide on a shared tmp path. We can't
    truly race in a single test, but we can confirm mkstemp gives a unique
    suffix by inspecting that two consecutive writes still leave only the
    target file behind."""
    path = tmp_path / "chap-checker.state.json"
    save_state(path, StateFile())
    save_state(path, StateFile())
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_corrupt_json_returns_empty_and_warns(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "chap-checker.state.json"
    path.write_text("{not valid json", encoding="utf-8")

    # Logging propagation may have been disabled by an earlier configure_logging;
    # capture directly on the chap_checker logger for this assertion.
    import logging as _logging

    logger = _logging.getLogger("chap_checker")
    prev_propagate = logger.propagate
    logger.propagate = True
    try:
        with caplog.at_level(_logging.WARNING, logger="chap_checker.state_store"):
            state = load_state(path)
    finally:
        logger.propagate = prev_propagate

    assert state.states == {}
    assert any("unreadable" in r.message for r in caplog.records)


def test_load_schema_mismatch_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "chap-checker.state.json"
    path.write_text(json.dumps({"version": 999, "states": "not a dict"}), encoding="utf-8")
    state = load_state(path)
    assert state.states == {}


def test_save_state_creates_missing_parent_dirs(tmp_path: Path) -> None:
    """A misconfigured --state path with a non-existent parent should still work."""
    nested = tmp_path / "deep" / "nested" / "dir"
    assert not nested.exists()
    target = nested / "chap-checker.state.json"
    save_state(target, StateFile())
    assert target.exists()
    assert load_state(target).states == {}


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


def test_intra_failure_change_does_not_emit_transition() -> None:
    """FAIL -> ERROR is a tweak to a sustained outage; don't re-alert."""
    now = datetime.now(UTC)
    previous = StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=now)})
    reports = [_report("prod", _result("ping", Status.ERROR))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL, Status.ERROR, Status.WARN}, now)

    assert transitions == []
    # State is still updated so the next OK can produce a recovery alert.
    assert new_state.states["prod::ping"].status is Status.ERROR


def test_warn_in_notify_on_fires_on_anomaly() -> None:
    now = datetime.now(UTC)
    previous = StateFile()
    reports = [_report("prod", _result("ping", Status.WARN, "missing version field"))]
    transitions, _ = compute_transitions(previous, reports, {Status.FAIL, Status.ERROR, Status.WARN}, now)

    assert len(transitions) == 1
    assert transitions[0].current_status is Status.WARN


def test_ok_to_skipped_does_not_transition_and_does_not_persist_skipped() -> None:
    now = datetime.now(UTC)
    previous = StateFile()  # implicit OK
    reports = [_report("prod", _result("system-info", Status.SKIPPED, "Skipped: ping not OK."))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL, Status.ERROR, Status.WARN}, now)

    assert transitions == []
    # SKIPPED is never persisted; there was no prior state, so nothing to track.
    assert "prod::system-info" not in new_state.states


def test_fail_to_skipped_preserves_failure_for_eventual_recovery() -> None:
    """The interesting case: chap-route was FAIL; an upstream prereq fails so it's
    now SKIPPED; the recovery alert must still fire when chap-route comes back OK."""
    now = datetime.now(UTC)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    state_a = StateFile(states={"prod::chap-route": CheckState(status=Status.FAIL, since=t0)})

    # Run with chap-route SKIPPED (e.g. because ping is down).
    reports_b = [_report("prod", _result("chap-route", Status.SKIPPED, "Skipped: ping not OK."))]
    transitions_b, state_b = compute_transitions(state_a, reports_b, {Status.FAIL, Status.ERROR, Status.WARN}, now)
    assert transitions_b == []
    # State must remember the FAIL, not overwrite with SKIPPED.
    assert state_b.states["prod::chap-route"].status is Status.FAIL

    # Now chap-route comes back OK.
    reports_c = [_report("prod", _result("chap-route", Status.OK, "back"))]
    transitions_c, state_c = compute_transitions(state_b, reports_c, {Status.FAIL, Status.ERROR, Status.WARN}, now)
    assert len(transitions_c) == 1
    assert transitions_c[0].kind == "recovery"
    assert state_c.states["prod::chap-route"].status is Status.OK


def test_skipped_to_ok_does_not_transition_when_no_prior_failure() -> None:
    """If the check never had a real prior status, SKIPPED -> OK is silent."""
    now = datetime.now(UTC)
    previous = StateFile()  # no prior state
    reports = [_report("prod", _result("chap-route", Status.OK, "back"))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL, Status.ERROR, Status.WARN}, now)

    # First-ever sighting is OK; no transition.
    assert transitions == []
    # State is preserved (curr_status == prev_status implicit OK).
    assert "prod::chap-route" in new_state.states


def test_partial_run_preserves_unseen_states() -> None:
    """Verify --instance / --check share the state file with the full run.

    A partial run that only reports on one instance must NOT drop the
    other instances' states - otherwise the next full run sees those
    states as missing, treats a sustained failure as fresh, and
    re-alerts on something the operator was already notified about.
    """
    now = datetime.now(UTC)
    earlier = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
    # Two instances were known previously: prod (currently failing) and
    # staging (currently OK). The current run only inspects prod.
    previous = StateFile(
        states={
            "prod::ping": CheckState(status=Status.FAIL, since=earlier),
            "staging::ping": CheckState(status=Status.OK, since=earlier),
        },
    )
    reports = [_report("prod", _result("ping", Status.FAIL, "still down"))]
    transitions, new_state = compute_transitions(previous, reports, {Status.FAIL}, now)

    # No alert: prod is sustained FAIL, staging wasn't run.
    assert transitions == []
    # Both instances' state survives the partial run.
    assert new_state.states["prod::ping"].status is Status.FAIL
    assert new_state.states["prod::ping"].since == earlier  # since unchanged
    assert new_state.states["staging::ping"].status is Status.OK
    assert new_state.states["staging::ping"].since == earlier


def test_partial_run_does_not_resurrect_fail_as_new_first_failure() -> None:
    """After a partial run, the next full run sees the preserved state.

    Regression guard: before the carry-forward, a partial ``--instance
    prod`` run would drop ``staging::ping`` from the state file, and the
    next full run treated staging's sustained FAIL as a fresh first-
    failure and re-alerted.
    """
    now = datetime.now(UTC)
    earlier = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
    previous = StateFile(
        states={
            "staging::ping": CheckState(status=Status.FAIL, since=earlier),
        },
    )
    # Partial run: only prod, doesn't touch staging.
    _, after_partial = compute_transitions(
        previous,
        [_report("prod", _result("ping", Status.OK, "fine"))],
        {Status.FAIL},
        now,
    )
    # Now a full run with both targets, staging still failing.
    full_reports = [
        _report("prod", _result("ping", Status.OK, "fine")),
        _report("staging", _result("ping", Status.FAIL, "still down")),
    ]
    transitions, _ = compute_transitions(after_partial, full_reports, {Status.FAIL}, now)
    # Critical: no re-alert for staging - the partial run preserved its
    # FAIL state so the full run sees it as sustained, not fresh.
    assert transitions == []
