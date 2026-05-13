"""Slack Incoming Webhook alerter."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel

from chap_checker.alerts.base import Transition
from chap_checker.logging import get_logger

_log = get_logger("alerts.slack")


class SlackBlockText(BaseModel):
    """Slack Block Kit text element (``plain_text`` or ``mrkdwn``)."""

    type: Literal["plain_text", "mrkdwn"]
    text: str


class SlackBlock(BaseModel):
    """Single Slack Block Kit block (header or section)."""

    type: Literal["header", "section"]
    text: SlackBlockText


class SlackPayload(BaseModel):
    """Body posted to a Slack Incoming Webhook."""

    text: str
    blocks: list[SlackBlock]


class SlackAlerter:
    """POST a Block Kit message to a Slack Incoming Webhook URL.

    Failures are logged but never raised - alert delivery must not affect the
    cron run's exit code.
    """

    name = "slack"

    def __init__(
        self,
        webhook_url: str,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout_s = timeout_s
        self._transport = transport

    async def notify(self, transitions: list[Transition]) -> None:
        if not transitions:
            return
        payload = _build_payload(transitions)
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
                response = await client.post(self._webhook_url, json=payload.model_dump(mode="json"))
                if response.status_code >= 400:
                    _log.warning(
                        "slack webhook returned %s: %s",
                        response.status_code,
                        response.text[:200],
                    )
        except Exception:  # noqa: BLE001 - never propagate
            _log.exception("slack notify failed")


def _build_payload(transitions: list[Transition]) -> SlackPayload:
    failures = [t for t in transitions if t.kind == "failure"]
    recoveries = [t for t in transitions if t.kind == "recovery"]
    summary = f"chap-checker: {len(failures)} new failure(s), {len(recoveries)} recovery"

    blocks: list[SlackBlock] = [
        SlackBlock(type="header", text=SlackBlockText(type="plain_text", text=summary)),
    ]
    for group, label in ((failures, "FAILURE"), (recoveries, "RECOVERY")):
        for t in group:
            blocks.append(
                SlackBlock(
                    type="section",
                    text=SlackBlockText(
                        type="mrkdwn",
                        text=(
                            f"*{label}*  `{t.target_name}`  {t.target_url}\n"
                            f"  `{t.check_name}`  {t.current_status.value.upper()}  {t.message}"
                        ),
                    ),
                )
            )

    return SlackPayload(text=summary, blocks=blocks)
