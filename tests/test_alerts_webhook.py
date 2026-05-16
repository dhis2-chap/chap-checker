"""Tests for the generic `WebhookAlerter` and its config plumbing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from chap_checker.alerts.base import Transition
from chap_checker.alerts.webhook import WebhookAlerter
from chap_checker.checks.base import Status


def _failure() -> Transition:
    return Transition(
        kind="failure",
        target_name="prod",
        target_url="https://prod.example",
        check_name="dhis2_ping",
        previous_status=Status.OK,
        current_status=Status.FAIL,
        message="down",
        duration_ms=12.3,
        occurred_at=datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC),
    )


def _recovery() -> Transition:
    return Transition(
        kind="recovery",
        target_name="prod",
        target_url="https://prod.example",
        check_name="dhis2_ping",
        previous_status=Status.FAIL,
        current_status=Status.OK,
        message="back",
        duration_ms=10.0,
        occurred_at=datetime(2026, 5, 16, 12, 5, 0, tzinfo=UTC),
    )


def test_canonical_envelope_shape() -> None:
    """Body matches the documented canonical envelope; receiver can parse blind."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = request.read().decode()
        return httpx.Response(200)

    alerter = WebhookAlerter(
        url="https://hook.example/notify",
        headers={"Authorization": "Bearer secret-123"},
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(alerter.notify([_failure(), _recovery()]))

    assert captured["url"] == "https://hook.example/notify"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer secret-123"  # httpx lowercases header names

    import json

    body = json.loads(str(captured["json"]))
    assert body["summary"] == {"failures": 1, "recoveries": 1}
    assert len(body["transitions"]) == 2
    first = body["transitions"][0]
    assert first["kind"] == "failure"
    assert first["target_name"] == "prod"
    assert first["check_name"] == "dhis2_ping"
    assert first["previous_status"] == "ok"
    assert first["current_status"] == "fail"
    assert first["message"] == "down"
    # `occurred_at` serializes as ISO-8601 (model_dump mode='json').
    assert first["occurred_at"].startswith("2026-05-16T12:00:00")


def test_empty_transitions_is_noop() -> None:
    """An empty list does not hit the wire (matches Slack semantics)."""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    alerter = WebhookAlerter(url="https://hook.example", transport=httpx.MockTransport(handler))
    asyncio.run(alerter.notify([]))
    assert calls == 0


def test_5xx_raises_so_dispatcher_skips_state_save() -> None:
    """The dispatcher relies on the alerter raising on >=400 to skip the state save and retry next run."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    alerter = WebhookAlerter(url="https://hook.example", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(alerter.notify([_failure()]))


def test_4xx_also_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    alerter = WebhookAlerter(
        url="https://hook.example",
        headers={"Authorization": "Bearer nope"},
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(alerter.notify([_failure()]))


def test_subclass_overrides_payload_shape() -> None:
    """The `_build_payload` hook is the documented extension point - subclasses keep the HTTP plumbing."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200)

    class CustomWebhook(WebhookAlerter):
        def _build_payload(self, transitions: list[Transition]) -> dict[str, object]:
            return {"transitions_seen": len(transitions), "first_check": transitions[0].check_name}

    alerter = CustomWebhook(url="https://hook.example", transport=httpx.MockTransport(handler))
    asyncio.run(alerter.notify([_failure(), _failure()]))

    import json

    assert json.loads(str(captured["body"])) == {
        "transitions_seen": 2,
        "first_check": "dhis2_ping",
    }
