"""Persisted check status and transition detection for stateful alerting.

The state file is a small JSON document recording the most recent status of
every ``(target, check)`` pair, plus a per-receiver ledger of open alerts.
On each run we diff the previous snapshot against the current results and
emit :class:`Transition` objects per receiver only when that receiver's
alert opens or closes, which keeps Slack quiet during sustained outages.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import AsyncGenerator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Literal

from pydantic import BaseModel, Field, ValidationError

from chap_checker.alerts.base import Transition, TransitionKind
from chap_checker.checks.base import Status
from chap_checker.logging import get_logger
from chap_checker.runner import RunReport

DEFAULT_STATE_FILENAME = "chap-checker.state.json"

_log = get_logger("state_store")


class StateLockTimeout(RuntimeError):
    """Raised when ``alert_state_lock()`` can't acquire the lock in time.

    Wraps the contended-state-file case so callers can react (skip
    dispatch this tick, log + retry next tick) instead of crashing the
    background loop. Carries the path of the lock file for ops triage.
    """

    def __init__(self, lock_path: Path, timeout_s: float) -> None:
        super().__init__(
            f"Could not acquire alert-state lock at {lock_path} within {timeout_s:.1f}s. "
            f"Another chap-checker process is holding the lock; remove it manually only if "
            f"you are certain no process owns it.",
        )
        self.lock_path = lock_path
        self.timeout_s = timeout_s


@asynccontextmanager
async def alert_state_lock(state_path: Path, *, timeout_s: float = 30.0) -> AsyncGenerator[None]:
    """File lock around the alert-state load->compute->dispatch->save window.

    Two concurrent ``chap-checker`` runs (overlapping cron tick + a manual
    invocation; ``tui`` and ``serve`` pointed at the same state file) can
    each read the same prior state, compute the same transitions, and
    dispatch the same alerts. The actual file write at the end of
    :func:`save_state` is atomic via ``os.replace``, but the window between
    :func:`load_state` and that replace is the race.

    We use a sidecar ``<state>.lock`` so the state file itself is not
    locked while it is being atomically replaced (the FD would be
    invalidated). ``fcntl.flock`` is POSIX-only; on Windows the context
    manager is a no-op and a deployment note in the alerting guide tells
    operators to keep alert dispatch single-process.

    Polls with ``LOCK_NB`` + ``asyncio.sleep`` so the asyncio event loop
    keeps ticking while we wait - matters for ``serve``, where this is
    called from the background refresh task. ``timeout_s`` bounds how
    long we wait before raising :class:`StateLockTimeout`; the caller
    should log and skip dispatch (don't crash the loop on a stuck peer).
    """
    if os.name != "posix":
        # Windows / unsupported - skip locking. Documented in
        # `docs/guides/alerts.md` so cron operators on Windows know
        # to serialise dispatch externally.
        yield
        return

    import fcntl  # POSIX-only import kept inside the function

    lock_path = Path(str(state_path) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # ``a+`` keeps the file even if another holder has it open; we
        # never write to the lock file, only to its lock byte range.
        handle: IO[str] = lock_path.open("a+")
    except OSError as exc:
        # Parent isn't a directory / no permission / disk full - state
        # writes will fail too, but :func:`save_state` swallows that.
        # Fall through to the no-lock path so alert delivery still
        # happens on a broken --state. Same contract the dispatch
        # function already has for state-write failures.
        _log.warning(
            "alert-state lock at %s could not be opened (%s); proceeding without it",
            lock_path,
            exc,
        )
        yield
        return
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StateLockTimeout(lock_path, timeout_s) from None
                await asyncio.sleep(0.1)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class CheckState(BaseModel):
    """Persisted status of one ``(target, check)`` pair."""

    status: Status
    since: datetime


class StateFile(BaseModel):
    """Top-level state-file schema.

    ``states`` holds the last real (non-SKIPPED) status of every
    ``(target, check)`` pair, shared by all receivers.

    ``receivers`` is the per-receiver alert ledger: for each alerter name,
    the ``(target, check)`` keys that receiver currently has an *open*
    alert for (a failure was sent and no recovery has followed yet). The
    stored :class:`CheckState` records the status and time the failure was
    announced.

    ``receivers`` is ``None`` for state files written before the ledger
    existed (chap-checker 0.10.x and earlier). Such files still load; the
    first dispatch seeds each receiver's ledger from ``states`` (see
    :func:`receiver_ledger`). Older chap-checker versions reading a newer
    file ignore the extra key, so the format stays ``version: 1`` in both
    directions.
    """

    version: Literal[1] = 1
    states: dict[str, CheckState] = Field(default_factory=dict)
    receivers: dict[str, dict[str, CheckState]] | None = None


@dataclass(frozen=True)
class ReceiverSpec:
    """What one alert receiver (a configured alerter) listens to.

    ``notify_on`` is the receiver's configured status filter. Only its
    non-OK members (``warn`` / ``fail`` / ``error``) matter: they form the
    receiver's *alerting set*. ``ok`` and ``skipped`` in ``notify_on`` are
    ignored - a recovery is always sent for a failure the receiver was
    told about, and never otherwise.

    ``targets`` is the set of target names that opted into this receiver
    (``alerts = [...]`` on the instance), or ``None`` for "every target".
    """

    notify_on: frozenset[Status]
    targets: frozenset[str] | None = None

    @property
    def alerting_set(self) -> frozenset[Status]:
        """Statuses that open an alert for this receiver."""
        return self.notify_on - {Status.OK, Status.SKIPPED}


def default_state_path() -> Path:
    """Path the CLI uses when no ``--state`` is given: ``./chap-checker.state.json``."""
    return Path.cwd() / DEFAULT_STATE_FILENAME


def load_state(path: Path) -> StateFile:
    """Load state from ``path``, or return an empty :class:`StateFile`.

    A corrupt (invalid JSON or invalid UTF-8) or schema-incompatible state
    file logs a warning and is treated as empty - alert bookkeeping must not
    fail a cron run that is otherwise fine. The file is left in place for
    human inspection; delete it manually once you've checked.
    """
    if not path.exists():
        return StateFile()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return StateFile.model_validate(data)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, OSError) as exc:
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


def receiver_ledger(previous: StateFile, name: str, spec: ReceiverSpec) -> dict[str, CheckState]:
    """Return (a copy of) the open-alert ledger receiver ``name`` starts a run with.

    - Receiver already tracked: its stored ledger.
    - Receiver not tracked yet in a current-format file (newly configured
      alerter): empty, so any check currently in its alerting set is
      announced on the first run.
    - Legacy state file (``receivers is None``, written before the ledger
      existed): seeded from the shared ``states``. The old dispatcher
      announced every entry into a status a receiver listened to, so the
      best reconstruction is "an alert is open for every key whose last
      status is in the receiver's alerting set". This keeps an in-flight
      outage from re-alerting after an upgrade and still delivers its
      recovery.
    """
    if previous.receivers is None:
        alerting = spec.alerting_set
        return {key: state for key, state in previous.states.items() if state.status in alerting}
    return dict(previous.receivers.get(name, {}))


def compute_receiver_transitions(
    previous: StateFile,
    reports: list[RunReport],
    receivers: Mapping[str, ReceiverSpec],
    now: datetime,
) -> tuple[dict[str, list[Transition]], StateFile]:
    """Diff ``previous`` against ``reports`` per receiver.

    Returns ``({receiver_name: transitions}, new_state)``; every name in
    ``receivers`` is a key of the returned dict (possibly with an empty
    list).

    Each receiver keeps its own ledger of open alerts, so receivers with
    different ``notify_on`` filters never shadow each other. For one
    receiver with alerting set ``A`` (see :class:`ReceiverSpec`) and one
    observed ``(target, check)`` status ``s``:

    - ``s`` in ``A`` and no open alert: send a **failure** and open an
      alert. This covers entering ``A`` from OK *and* from a non-OK status
      outside ``A`` (OK -> WARN -> FAIL with ``notify_on = ["fail"]``
      alerts on the FAIL).
    - ``s`` is OK and an alert is open: send a **recovery** and close it.
    - Anything else is silent: a change inside ``A`` (FAIL -> ERROR), a
      move from ``A`` to a non-OK status outside ``A`` (FAIL -> WARN with
      only ``fail`` selected: the alert stays open and its recovery is sent
      once the check returns to OK, so FAIL -> WARN -> FAIL does not
      re-alert), OK -> OK, or a non-OK status outside ``A`` with no open
      alert.
    - ``SKIPPED`` never changes anything.

    A recovery is therefore only ever sent to a receiver that was sent the
    matching failure, and no entry into ``A`` without an open alert is
    hidden.

    Targets that did not opt into a receiver have their ledger entries
    dropped for the checks observed in this run, so opting in later
    announces an outage still in progress instead of sending a lone
    recovery.

    The shared ``states`` map tracks the last real status for every
    ``(target, check)`` (``SKIPPED`` is never persisted) and supplies
    ``previous_status`` on emitted transitions. Keys and receivers not
    observed in this run are carried forward unchanged, so partial runs
    (``--instance`` / ``--check``) never drop state.
    """
    transitions: dict[str, list[Transition]] = {name: [] for name in receivers}
    ledgers: dict[str, dict[str, CheckState]] = {
        name: receiver_ledger(previous, name, spec) for name, spec in receivers.items()
    }
    new_states: dict[str, CheckState] = {}

    for report in reports:
        for result in report.results:
            key = _state_key(report.target_name, result.name)
            prev = previous.states.get(key)
            prev_status = prev.status if prev is not None else Status.OK
            curr_status = result.status

            # Shared status bookkeeping. SKIPPED is informational: don't
            # overwrite a real status with it; with no prior state, don't
            # track the entry yet.
            if curr_status is Status.SKIPPED:
                if prev is not None:
                    new_states[key] = prev
            elif prev is not None and curr_status == prev.status:
                new_states[key] = prev
            else:
                new_states[key] = CheckState(status=curr_status, since=now)

            for name, spec in receivers.items():
                ledger = ledgers[name]
                if spec.targets is not None and report.target_name not in spec.targets:
                    ledger.pop(key, None)
                    continue
                if curr_status is Status.SKIPPED:
                    continue
                is_open = key in ledger
                kind: TransitionKind
                if curr_status in spec.alerting_set and not is_open:
                    kind = "failure"
                    ledger[key] = CheckState(status=curr_status, since=now)
                elif curr_status is Status.OK and is_open:
                    kind = "recovery"
                    del ledger[key]
                else:
                    continue
                transitions[name].append(
                    Transition(
                        kind=kind,
                        target_name=report.target_name,
                        target_display_name=report.target_display_name,
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

    # Ledgers of receivers not configured in this run are carried forward
    # as well, so an alerter that is commented out for a while keeps its
    # open alerts and doesn't re-announce them when it comes back.
    new_receivers: dict[str, dict[str, CheckState]] = dict(previous.receivers or {})
    new_receivers.update(ledgers)

    return transitions, StateFile(states=new_states, receivers=new_receivers)


def compute_transitions(
    previous: StateFile,
    reports: list[RunReport],
    notify_on: Iterable[Status],
    now: datetime,
    *,
    receiver: str = "default",
) -> tuple[list[Transition], StateFile]:
    """Single-receiver convenience wrapper around :func:`compute_receiver_transitions`.

    Every target counts as opted in; the receiver's ledger is stored under
    ``receiver`` in the returned state.
    """
    spec = ReceiverSpec(notify_on=frozenset(notify_on))
    by_receiver, new_state = compute_receiver_transitions(previous, reports, {receiver: spec}, now)
    return by_receiver[receiver], new_state
