"""Alerter protocol and the Transition pydantic model that drives it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict

from chap_checker.checks.base import Status

if TYPE_CHECKING:
    # `config_model` points each alerter at its TOML schema so the
    # registry can describe `[alerts.<name>]` properties without
    # importing chap_checker.config (which would be a cycle).
    pass

TransitionKind = Literal["failure", "recovery"]


class Transition(BaseModel):
    """A single status change worth alerting on.

    Emitted by :func:`chap_checker.state_store.compute_transitions` when a
    check's status flips between OK and a non-OK family member.
    """

    kind: TransitionKind
    target_name: str
    target_display_name: str | None = None
    target_url: str
    check_name: str
    previous_status: Status
    current_status: Status
    message: str
    duration_ms: float
    occurred_at: datetime


@runtime_checkable
class Alerter(Protocol):
    """Protocol all alerters implement.

    Implementations *may* raise on delivery failure - the dispatcher
    (:func:`chap_checker.cli._dispatch_alerts`) catches the exception, logs
    it, and skips the state-file save so the transition is retried on the
    next run. Alert delivery failures never change the run's exit code
    regardless of whether the alerter raises or swallows.

    `description` is the one-line text shown in `chap-checker alerts list`
    (the registry view), parallel to `Check.description`.
    """

    name: ClassVar[str]
    description: ClassVar[str]

    # Optional class attributes (not part of the runtime-checkable
    # surface, accessed via getattr by `chap-checker alerts list`):
    #
    # - `toml_example: ClassVar[str]` - a copy-paste-ready
    #   `[alerts.<name>]` snippet with inline `# comment` per field.
    #   Hand-curated so each alerter controls its own wording.
    # - `config_model: ClassVar[type[BaseModel] | None]` - the pydantic
    #   config model. Used by tools that want to introspect the schema
    #   programmatically; the human-facing view uses `toml_example`.

    async def notify(self, transitions: list[Transition]) -> None:
        """Send the given transitions out-of-band.

        May raise on transport / non-2xx responses; the dispatcher decides
        whether to surface the failure.
        """
        ...


class AlerterBinding(BaseModel):
    """Pairs a runtime :class:`Alerter` instance with the statuses it cares about."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    alerter: Alerter
    notify_on: set[Status]


_ALERTER_CLASSES: dict[str, type[Alerter]] = {}

_TAlerter = TypeVar("_TAlerter", bound=type[Alerter])


def register_alerter(name: str) -> Callable[[_TAlerter], _TAlerter]:
    """Class decorator: register an alerter class under ``name``.

    This is currently a discovery aid - the CLI's per-alerter config wiring
    (:func:`chap_checker.cli._build_alerters`) still instantiates known
    alerters by hand, but the registry makes new alerters introspectable
    and is the seam for a future plugin-style config layer.

    Example:
        @register_alerter("slack")
        class SlackAlerter:
            name = "slack"
            ...
    """

    def deco(cls: _TAlerter) -> _TAlerter:
        _ALERTER_CLASSES[name] = cls
        return cls

    return deco


def alerter_class(name: str) -> type[Alerter] | None:
    """Return the registered alerter class for ``name``, or ``None``."""
    return _ALERTER_CLASSES.get(name)


def all_alerter_classes() -> dict[str, type[Alerter]]:
    """Return a copy of the alerter-class registry keyed by name."""
    return dict(_ALERTER_CLASSES)
