import asyncio
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chap_checker.checks.base import CheckResult, Status
from chap_checker.runner import RunReport
from chap_checker.state_store import (
    CheckState,
    ReceiverSpec,
    StateFile,
    StateLockTimeout,
    alert_state_lock,
    compute_receiver_transitions,
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


def test_concurrent_saves_use_unique_tmp_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Overlapping save_state calls must each write their own tmp file.

    Both writer threads are held at ``os.replace`` until each has fully
    written its tmp file, so the two writes genuinely overlap. A fixed
    tmp filename would make both threads hand the same source path to
    ``os.replace`` (and the second rename would then fail).
    """
    path = tmp_path / "chap-checker.state.json"
    real_replace = os.replace
    barrier = threading.Barrier(2, timeout=10)
    sources: list[str] = []
    lock = threading.Lock()

    def overlapping_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        with lock:
            sources.append(str(src))
        barrier.wait()  # both tmp files exist on disk at this point
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", overlapping_replace)

    now = datetime.now(UTC)
    states = [
        StateFile(states={"a::ping": CheckState(status=Status.FAIL, since=now)}),
        StateFile(states={"b::ping": CheckState(status=Status.OK, since=now)}),
    ]
    errors: list[BaseException] = []

    def writer(state: StateFile) -> None:
        try:
            save_state(path, state)
        except BaseException as exc:  # noqa: BLE001 - surfaced via the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(s,)) for s in states]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    assert len(sources) == 2
    assert sources[0] != sources[1]
    # The survivor is one of the two complete documents, never a mix.
    assert load_state(path).states.keys() in ({"a::ping"}, {"b::ping"})
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


def test_load_invalid_utf8_returns_empty(tmp_path: Path) -> None:
    """A binary-corrupted file (not UTF-8) follows the unreadable-state policy."""
    path = tmp_path / "chap-checker.state.json"
    path.write_bytes(b'{"version": 1, "states": {"\xff\xfe\x00\x80": 1}}')
    state = load_state(path)
    assert state.states == {}
    # Left on disk for inspection.
    assert path.exists()


def test_load_legacy_format_without_receivers(tmp_path: Path) -> None:
    """State files written before the per-receiver ledger still load."""
    path = tmp_path / "chap-checker.state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "states": {
                    "prod::ping": {"status": "fail", "since": "2026-05-13T15:05:12Z"},
                    "prod::route": {"status": "warn", "since": "2026-05-13T15:05:12Z"},
                },
            }
        ),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.receivers is None
    assert state.states["prod::ping"].status is Status.FAIL

    now = datetime.now(UTC)
    reports = [_report("prod", _result("ping", Status.FAIL), _result("route", Status.FAIL))]
    transitions, new_state = compute_transitions(state, reports, {Status.FAIL}, now, receiver="slack")
    # ping was already FAIL under the old format: assumed announced, no re-alert.
    # route was WARN (not selected) and is now FAIL: that entry is announced.
    assert [(t.check_name, t.kind) for t in transitions] == [("route", "failure")]
    assert new_state.receivers is not None
    assert set(new_state.receivers["slack"]) == {"prod::ping", "prod::route"}

    # The in-flight legacy outage still gets its recovery.
    transitions, _ = compute_transitions(
        new_state, [_report("prod", _result("ping", Status.OK))], {Status.FAIL}, now, receiver="slack"
    )
    assert [(t.check_name, t.kind) for t in transitions] == [("ping", "recovery")]


def test_new_format_round_trips(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    path = tmp_path / "chap-checker.state.json"
    _, state = compute_transitions(StateFile(), [_report("prod", _result("ping", Status.FAIL))], {Status.FAIL}, now)
    save_state(path, state)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["receivers"]["default"]["prod::ping"]["status"] == "fail"
    assert load_state(path) == state


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


# ---------- per-receiver alert semantics ----------


def _run_sequence(
    statuses: list[Status],
    receivers: dict[str, ReceiverSpec],
    target: str = "prod",
) -> list[dict[str, list[tuple[str, Status, Status]]]]:
    """Feed one check through ``statuses``; return per-step (kind, prev, curr) per receiver."""
    state = StateFile()
    out: list[dict[str, list[tuple[str, Status, Status]]]] = []
    for i, status in enumerate(statuses):
        now = datetime(2026, 6, 1, 12, i, tzinfo=UTC)
        by_receiver, state = compute_receiver_transitions(
            state, [_report(target, _result("ping", status))], receivers, now
        )
        out.append(
            {name: [(t.kind, t.previous_status, t.current_status) for t in ts] for name, ts in by_receiver.items()}
        )
    return out


def _spec(*statuses: Status) -> ReceiverSpec:
    return ReceiverSpec(notify_on=frozenset(statuses))


def test_fail_only_filter_alerts_on_warn_then_fail() -> None:
    """Regression: OK -> WARN -> FAIL -> OK with notify_on=["fail"] used to
    swallow the failure (WARN -> FAIL was skipped as non-OK -> non-OK) and
    then send a recovery for a failure that was never announced."""
    steps = _run_sequence([Status.OK, Status.WARN, Status.FAIL, Status.OK], {"r": _spec(Status.FAIL)})
    assert [s["r"] for s in steps] == [
        [],
        [],
        [("failure", Status.WARN, Status.FAIL)],
        [("recovery", Status.FAIL, Status.OK)],
    ]


def test_no_recovery_without_preceding_failure() -> None:
    """OK -> WARN -> OK with notify_on=["fail"] is silent end to end."""
    steps = _run_sequence([Status.OK, Status.WARN, Status.OK], {"r": _spec(Status.FAIL)})
    assert [s["r"] for s in steps] == [[], [], []]


def test_downgrade_out_of_selected_set_keeps_alert_open() -> None:
    """FAIL -> WARN with only fail selected: quiet, alert stays open.

    A later FAIL does not re-alert (the receiver was never told the outage
    ended) and the eventual OK sends exactly one recovery.
    """
    steps = _run_sequence(
        [Status.FAIL, Status.WARN, Status.FAIL, Status.WARN, Status.OK],
        {"r": _spec(Status.FAIL)},
    )
    assert [s["r"] for s in steps] == [
        [("failure", Status.OK, Status.FAIL)],
        [],
        [],
        [],
        [("recovery", Status.WARN, Status.OK)],
    ]


def test_two_receivers_with_different_filters() -> None:
    """Each receiver's filter is applied to its own ledger; neither shadows the other."""
    receivers = {
        "slack": _spec(Status.FAIL, Status.ERROR, Status.WARN),
        "webhook": _spec(Status.FAIL),
    }
    steps = _run_sequence([Status.OK, Status.WARN, Status.FAIL, Status.WARN, Status.OK], receivers)
    assert [s["slack"] for s in steps] == [
        [],
        [("failure", Status.OK, Status.WARN)],
        [],  # already alerting; WARN -> FAIL is a change inside the outage
        [],
        [("recovery", Status.WARN, Status.OK)],
    ]
    assert [s["webhook"] for s in steps] == [
        [],
        [],
        [("failure", Status.WARN, Status.FAIL)],
        [],
        [("recovery", Status.WARN, Status.OK)],
    ]


def test_ok_in_notify_on_is_ignored() -> None:
    """``ok`` in notify_on never opens an alert nor sends a stray recovery."""
    steps = _run_sequence([Status.OK, Status.OK], {"r": _spec(Status.OK)})
    assert [s["r"] for s in steps] == [[], []]


def test_skipped_does_not_touch_receiver_ledger() -> None:
    steps = _run_sequence([Status.FAIL, Status.SKIPPED, Status.OK], {"r": _spec(Status.FAIL)})
    assert [s["r"] for s in steps] == [
        [("failure", Status.OK, Status.FAIL)],
        [],
        [("recovery", Status.FAIL, Status.OK)],
    ]


def test_target_not_opted_in_has_no_open_alert() -> None:
    """Opting in during an outage announces it rather than sending a lone recovery."""
    now = datetime.now(UTC)
    reports = [_report("prod", _result("ping", Status.FAIL))]
    silent = {"r": ReceiverSpec(notify_on=frozenset({Status.FAIL}), targets=frozenset())}
    opted = {"r": ReceiverSpec(notify_on=frozenset({Status.FAIL}), targets=frozenset({"prod"}))}

    by_receiver, state = compute_receiver_transitions(StateFile(), reports, silent, now)
    assert by_receiver == {"r": []}
    assert state.states["prod::ping"].status is Status.FAIL

    by_receiver, state = compute_receiver_transitions(state, reports, opted, now)
    assert [t.kind for t in by_receiver["r"]] == ["failure"]

    by_receiver, _ = compute_receiver_transitions(state, [_report("prod", _result("ping", Status.OK))], opted, now)
    assert [t.kind for t in by_receiver["r"]] == ["recovery"]


def test_newly_configured_receiver_is_told_about_ongoing_outage() -> None:
    now = datetime.now(UTC)
    reports = [_report("prod", _result("ping", Status.FAIL))]
    _, state = compute_receiver_transitions(StateFile(), reports, {"slack": _spec(Status.FAIL)}, now)

    both = {"slack": _spec(Status.FAIL), "webhook": _spec(Status.FAIL)}
    by_receiver, state = compute_receiver_transitions(state, reports, both, now)
    assert by_receiver["slack"] == []
    assert [t.kind for t in by_receiver["webhook"]] == ["failure"]


def test_unconfigured_receiver_ledger_is_carried_forward() -> None:
    now = datetime.now(UTC)
    reports = [_report("prod", _result("ping", Status.FAIL))]
    _, state = compute_receiver_transitions(StateFile(), reports, {"slack": _spec(Status.FAIL)}, now)
    _, state = compute_receiver_transitions(state, reports, {"webhook": _spec(Status.FAIL)}, now)
    assert state.receivers is not None
    assert "prod::ping" in state.receivers["slack"]


# ---------- alert_state_lock ----------


@pytest.mark.skipif(os.name != "posix", reason="alert_state_lock is POSIX-only")
def test_alert_state_lock_serialises_holders(tmp_path: Path) -> None:
    """Two concurrent acquires never overlap inside the protected region."""
    state_path = tmp_path / "chap-checker.state.json"
    in_critical = 0
    max_concurrent = 0

    async def hold(delay_s: float) -> None:
        nonlocal in_critical, max_concurrent
        async with alert_state_lock(state_path):
            in_critical += 1
            max_concurrent = max(max_concurrent, in_critical)
            await asyncio.sleep(delay_s)
            in_critical -= 1

    async def main() -> None:
        await asyncio.gather(hold(0.2), hold(0.2), hold(0.2))

    asyncio.run(main())
    assert max_concurrent == 1


@pytest.mark.skipif(os.name != "posix", reason="alert_state_lock is POSIX-only")
def test_alert_state_lock_timeout_raises(tmp_path: Path) -> None:
    """If the lock is held longer than ``timeout_s`` the second acquire raises."""
    state_path = tmp_path / "chap-checker.state.json"

    async def hold_long() -> None:
        async with alert_state_lock(state_path):
            await asyncio.sleep(1.0)

    async def try_short() -> Exception | None:
        # Sleep first to let `hold_long` grab the lock; then try with a
        # tight timeout.
        await asyncio.sleep(0.1)
        try:
            async with alert_state_lock(state_path, timeout_s=0.2):
                pass
        except StateLockTimeout as exc:
            return exc
        return None

    async def main() -> Exception | None:
        long_task = asyncio.create_task(hold_long())
        result = await try_short()
        await long_task
        return result

    raised = asyncio.run(main())
    assert isinstance(raised, StateLockTimeout)
