"""Persisted check status and transition detection for stateful alerting.

The state file is a small JSON document recording the most recent status of
every ``(target, check)`` pair. On each run we diff the previous snapshot
against the current results and emit :class:`Transition` objects only when a
status flips, which keeps Slack quiet during sustained outages.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from chap_checker.alerts.base import Transition, TransitionKind
from chap_checker.checks.base import Status
from chap_checker.runner import RunReport

DEFAULT_STATE_FILENAME = "chap-checker.state.json"


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
    """Load state from ``path``, or return an empty :class:`StateFile` if missing."""
    if not path.exists():
        return StateFile()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return StateFile.model_validate(data)


def save_state(path: Path, state: StateFile) -> None:
    """Atomically write ``state`` to ``path`` via tmp-file + ``os.replace``."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state.model_dump(mode="json"), f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


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

    return transitions, StateFile(states=new_states)
