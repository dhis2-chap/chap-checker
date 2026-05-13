from datetime import UTC, datetime
from pathlib import Path

import pytest

from chap_checker.alerts.base import Alerter, AlerterBinding, Transition
from chap_checker.checks.base import CheckResult, Status
from chap_checker.cli import _build_alerters, _dispatch_alerts
from chap_checker.config import AlertsConfig, SlackAlertConfig
from chap_checker.runner import RunReport
from chap_checker.state_store import CheckState, StateFile, load_state, save_state


class _FakeAlerter:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[Transition]] = []

    async def notify(self, transitions: list[Transition]) -> None:
        self.calls.append(list(transitions))


def _failing_report() -> RunReport:
    return RunReport(
        target_name="prod",
        target_url="https://prod.example",
        results=[CheckResult(name="ping", status=Status.FAIL, message="down", duration_ms=10.0)],
    )


def _recovering_report() -> RunReport:
    return RunReport(
        target_name="prod",
        target_url="https://prod.example",
        results=[CheckResult(name="ping", status=Status.OK, message="back", duration_ms=10.0)],
    )


def _alerts_cfg() -> AlertsConfig:
    return AlertsConfig(
        slack=SlackAlertConfig(
            webhook_url="https://hooks.slack.com/x",  # type: ignore[arg-type]
            notify_on=[Status.FAIL, Status.ERROR, Status.WARN],
        )
    )


def _binding(alerter: Alerter, *statuses: Status) -> AlerterBinding:
    return AlerterBinding(alerter=alerter, notify_on=set(statuses))


def test_build_alerters_returns_slack() -> None:
    bindings = _build_alerters(_alerts_cfg())
    assert len(bindings) == 1
    assert bindings[0].alerter.name == "slack"
    assert Status.FAIL in bindings[0].notify_on


def test_dispatch_fires_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = _FakeAlerter()
    monkeypatch.setattr(
        "chap_checker.cli._build_alerters",
        lambda _cfg: [_binding(fake, Status.FAIL, Status.ERROR, Status.WARN)],
    )

    state_path = tmp_path / "state.json"
    _dispatch_alerts([_failing_report()], _alerts_cfg(), state_path)

    assert len(fake.calls) == 1
    assert fake.calls[0][0].kind == "failure"
    assert state_path.exists()


def test_dispatch_silent_on_sustained_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = _FakeAlerter()
    monkeypatch.setattr(
        "chap_checker.cli._build_alerters",
        lambda _cfg: [_binding(fake, Status.FAIL, Status.ERROR, Status.WARN)],
    )

    state_path = tmp_path / "state.json"
    save_state(
        state_path,
        StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=datetime.now(UTC))}),
    )

    _dispatch_alerts([_failing_report()], _alerts_cfg(), state_path)
    assert fake.calls == []


def test_dispatch_emits_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = _FakeAlerter()
    monkeypatch.setattr(
        "chap_checker.cli._build_alerters",
        lambda _cfg: [_binding(fake, Status.FAIL, Status.ERROR, Status.WARN)],
    )

    state_path = tmp_path / "state.json"
    save_state(
        state_path,
        StateFile(states={"prod::ping": CheckState(status=Status.FAIL, since=datetime.now(UTC))}),
    )

    _dispatch_alerts([_recovering_report()], _alerts_cfg(), state_path)
    assert len(fake.calls) == 1
    assert fake.calls[0][0].kind == "recovery"

    # After recovery is recorded, next OK run is silent.
    fake.calls.clear()
    _dispatch_alerts([_recovering_report()], _alerts_cfg(), state_path)
    assert fake.calls == []


def test_alerter_exception_is_swallowed_but_state_is_not_saved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Slack outage must not crash the run, and must not commit the new state -
    next run will recompute the same transition and retry delivery."""

    class _BoomAlerter:
        name = "boom"

        async def notify(self, transitions: list[Transition]) -> None:
            raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "chap_checker.cli._build_alerters",
        lambda _cfg: [_binding(_BoomAlerter(), Status.FAIL)],
    )

    state_path = tmp_path / "state.json"
    # Pre-existing state: everything OK.
    save_state(
        state_path,
        StateFile(states={"prod::ping": CheckState(status=Status.OK, since=datetime.now(UTC))}),
    )

    # Dispatch must not raise (alert delivery never changes exit code).
    _dispatch_alerts([_failing_report()], _alerts_cfg(), state_path)

    # State must remain OK so the next run retries the failure transition.
    saved = load_state(state_path)
    assert saved.states["prod::ping"].status is Status.OK


def test_state_saved_when_no_transitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An all-OK run should still persist state so newly-seen checks are tracked."""
    fake = _FakeAlerter()
    monkeypatch.setattr(
        "chap_checker.cli._build_alerters",
        lambda _cfg: [_binding(fake, Status.FAIL)],
    )

    state_path = tmp_path / "state.json"
    _dispatch_alerts([_recovering_report()], _alerts_cfg(), state_path)

    assert fake.calls == []  # no transitions, nothing dispatched
    saved = load_state(state_path)
    assert saved.states["prod::ping"].status is Status.OK


def test_slack_config_resolves_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SLACK", "https://hooks.slack.com/from-env")
    cfg = SlackAlertConfig(webhook_url_env="MY_SLACK")
    assert cfg.resolve_webhook_url() == "https://hooks.slack.com/from-env"


def test_slack_config_rejects_both_sources() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SlackAlertConfig(
            webhook_url="https://hooks.slack.com/x",  # type: ignore[arg-type]
            webhook_url_env="ALSO",
        )


def test_slack_config_rejects_neither_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SlackAlertConfig()


def test_slack_config_parses_notify_on_strings() -> None:
    cfg = SlackAlertConfig.model_validate(
        {
            "webhook_url": "https://hooks.slack.com/x",
            "notify_on": ["fail", "error"],
        }
    )
    assert Status.FAIL in cfg.notify_on
    assert Status.WARN not in cfg.notify_on
