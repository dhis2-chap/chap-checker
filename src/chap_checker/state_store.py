"""Persisted check status and transition detection for stateful alerting.

The state file is a small JSON document recording the most recent status of
every ``(target, check)`` pair. On each run we diff the previous snapshot
against the current results and emit :class:`Transition` objects only when a
status flips, which keeps Slack quiet during sustained outages.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from chap_checker.alerts.base import Transition, TransitionKind
from chap_checker.checks.base import Status
from chap_checker.logging import get_logger
from chap_checker.runner import RunReport

DEFAULT_STATE_FILENAME = "chap-checker.state.json"

_log = get_logger("state_store")


class CheckState(BaseModel):
    """Persisted status of one ``(target, check)`` pair."""

    status: Status
    since: datetime


class StateFile(BaseModel):
    """Top-level state-file schema."""

    version: Literal[1] = 1
    states: dict[str, CheckState] = Field(default_factory=dict)


def default_state_path() -> Path:
    """Path the CLI uses when no ``--state`` is given: ``./chap-checker.state.json``."""
    return Path.cwd() / DEFAULT_STATE_FILENAME


def load_state(path: Path) -> StateFile:
    """Load state from ``path``, or return an empty :class:`StateFile`.

    A corrupt or schema-incompatible state file logs a warning and is treated
    as empty - alert bookkeeping must not fail a cron run that is otherwise
    fine. The file is left in place for human inspection; delete it manually
    once you've checked.
    """
    if not path.exists():
        return StateFile()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return StateFile.model_validate(data)
    except (json.JSONDecodeError, ValidationError, OSError) as exc:
        _log.warning(
            "state file %s is unreadable (%s); treating prior state as empty. "
            "Delete or move the file aside if this persists.",
            path,
            exc,
        )
        return StateFile()


def save_state(path: Path, state: StateFile) -> None:
    """Atomically write ``state`` to ``path``.

    Creates the parent directory if it doesn't exist (the file is typically
    written from cron, where a misconfigured ``--state /some/new/dir/...``
    path shouldn't crash dispatch).

    Uses a unique tmp file via :func:`tempfile.mkstemp` in the same directory
    so two concurrent ``chap-checker`` runs (overlapping cron tick + a manual
    invocation, say) don't clobber each other's tmp file or race on
    ``os.replace``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.model_dump(mode="json"), f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup; surface the original error.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _state_key(target_name: str, check_name: str) -> str:
    return f"{target_name}::{check_name}"


def compute_transitions(
    previous: StateFile,
    reports: list[RunReport],
    notify_on: Iterable[Status],
    now: datetime,
) -> tuple[list[Transition], StateFile]:
    """Diff ``previous`` against ``reports``; return (transitions, new state).

    A transition is emitted only when one side is ``OK`` and the other side is
    a non-OK status in ``notify_on``. Intra-failure changes (FAIL<->ERROR<->WARN)
    are tweaks to a sustained outage, not new alerts.

    ``SKIPPED`` is informational: it is never persisted to state (the last
    known *real* status is preserved) and it never produces a transition.
    Preserving the prior status across a transient SKIPPED run is what lets an
    eventual OK still fire a recovery alert even when an upstream prereq
    flapped in between.
    """
    notify_set = set(notify_on)
    transitions: list[Transition] = []
    new_states: dict[str, CheckState] = {}

    for report in reports:
        for result in report.results:
            key = _state_key(report.target_name, result.name)
            prev = previous.states.get(key)
            prev_status = prev.status if prev is not None else Status.OK
            curr_status = result.status

            # SKIPPED is informational. Don't overwrite a real status with it;
            # if there is no prior state, just don't track this entry yet.
            if curr_status is Status.SKIPPED:
                if prev is not None:
                    new_states[key] = prev
                continue

            if curr_status == prev_status:
                new_states[key] = prev if prev is not None else CheckState(status=curr_status, since=now)
                continue

            new_states[key] = CheckState(status=curr_status, since=now)

            # Only OK <-> failure-family transitions are alert-worthy.
            if curr_status is not Status.OK and prev_status is not Status.OK:
                continue

            # The non-OK side determines whether to alert.
            non_ok = curr_status if curr_status is not Status.OK else prev_status
            if non_ok not in notify_set:
                continue

            kind: TransitionKind = "recovery" if curr_status is Status.OK else "failure"
            transitions.append(
                Transition(
                    kind=kind,
                    target_name=report.target_name,
                    target_url=report.target_url,
                    check_name=result.name,
                    previous_status=prev_status,
                    current_status=curr_status,
                    message=result.message,
                    duration_ms=result.duration_ms,
                    occurred_at=now,
                )
            )

    # Carry forward state for (target, check) pairs not observed in this
    # run. Verify supports partial runs via ``--instance`` and ``--check``
    # which share the same state file as the full run; without this carry
    # the partial run would silently drop every other key, and the next
    # full run would treat a sustained failure as a fresh first-failure
    # and re-alert. Stale entries (instance removed from config, check
    # renamed) are a soft leak, not a correctness bug - a manual prune
    # is the right tool for that.
    for key, state in previous.states.items():
        new_states.setdefault(key, state)

    return transitions, StateFile(states=new_states)
