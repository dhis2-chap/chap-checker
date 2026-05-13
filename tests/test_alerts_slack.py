import asyncio
import json
from datetime import UTC, datetime

import httpx

from chap_checker.alerts.base import Transition
from chap_checker.alerts.slack import SlackAlerter
from chap_checker.checks.base import Status


def _failure() -> Transition:
    return Transition(
        kind="failure",
        target_name="prod",
        target_url="https://prod.example",
        check_name="ping",
        previous_status=Status.OK,
        current_status=Status.FAIL,
        message="down",
        duration_ms=42.0,
        occurred_at=datetime.now(UTC),
    )


def _recovery() -> Transition:
    return Transition(
        kind="recovery",
        target_name="prod",
        target_url="https://prod.example",
        check_name="ping",
        previous_status=Status.FAIL,
        current_status=Status.OK,
        message="back",
        duration_ms=12.0,
        occurred_at=datetime.now(UTC),
    )


def test_slack_posts_blocks_to_webhook() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text="ok")

    alerter = SlackAlerter(
        webhook_url="https://hooks.slack.com/services/T/B/X",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(alerter.notify([_failure(), _recovery()]))

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://hooks.slack.com/services/T/B/X"
    body = json.loads(request.content)
    assert "blocks" in body
    assert body["blocks"][0]["type"] == "header"
    assert "1 new failure(s), 1 recovery" in body["text"]


def test_slack_empty_transitions_skips_post() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    alerter = SlackAlerter(
        webhook_url="https://hooks.slack.com/x",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(alerter.notify([]))
    assert captured == []


def test_slack_swallows_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    alerter = SlackAlerter(
        webhook_url="https://hooks.slack.com/x",
        transport=httpx.MockTransport(handler),
    )
    # Should not raise.
    asyncio.run(alerter.notify([_failure()]))


def test_slack_swallows_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    alerter = SlackAlerter(
        webhook_url="https://hooks.slack.com/x",
        transport=httpx.MockTransport(handler),
    )
    # Should not raise.
    asyncio.run(alerter.notify([_failure()]))
