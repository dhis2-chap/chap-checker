"""Alerter protocol and the Transition pydantic model that drives it."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from chap_checker.checks.base import Status

TransitionKind = Literal["failure", "recovery"]


class Transition(BaseModel):
    """A single status change worth alerting on.

    Emitted by :func:`chap_checker.state_store.compute_transitions` when a
    check's status flips between OK and a non-OK family member.
    """

    kind: TransitionKind
    target_name: str
    target_url: str
    check_name: str
    previous_status: Status
    current_status: Status
    message: str
    duration_ms: float
    occurred_at: datetime


@runtime_checkable
class Alerter(Protocol):
    """Protocol all alerters implement."""

    name: str

    async def notify(self, transitions: list[Transition]) -> None:
        """Send the given transitions out-of-band.

        Implementations MUST NOT raise on delivery failure; log to stderr and
        return. A broken alert pipeline must never change the cron run's exit
        code semantics.
        """
        ...


class AlerterBinding(BaseModel):
    """Pairs a runtime :class:`Alerter` instance with the statuses it cares about."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    alerter: Alerter
    notify_on: set[Status]
