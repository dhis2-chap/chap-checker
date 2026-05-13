"""Slack Incoming Webhook alerter."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel

from chap_checker.alerts.base import Transition
from chap_checker.checks.base import Status
from chap_checker.logging import get_logger

_log = get_logger("alerts.slack")

# Slack's status colors (from their brand guide; render well on dark and light).
_COLOR_BY_STATUS: dict[Status, str] = {
    Status.OK: "#2EB67D",  # green
    Status.WARN: "#ECB22E",  # yellow
    Status.FAIL: "#E01E5A",  # red
    Status.ERROR: "#E01E5A",  # red (same family as FAIL)
}


class SlackBlockText(BaseModel):
    """Slack Block Kit text element (``plain_text`` or ``mrkdwn``)."""

    type: Literal["plain_text", "mrkdwn"]
    text: str


class SlackBlock(BaseModel):
    """Single Slack Block Kit block (header or section)."""

    type: Literal["header", "section"]
    text: SlackBlockText


class SlackAttachment(BaseModel):
    """Legacy Slack attachment that gives Block Kit a colored left border.

    Block Kit blocks on their own can't render the vertical color stripe;
    wrapping the same blocks in an attachment with ``color`` gets it back.
    """

    color: str
    blocks: list[SlackBlock]


class SlackPayload(BaseModel):
    """Body posted to a Slack Incoming Webhook."""

    text: str
    blocks: list[SlackBlock]
    attachments: list[SlackAttachment]


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
        raise_on_error: bool = False,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout_s = timeout_s
        self._transport = transport
        self._raise_on_error = raise_on_error

    async def notify(self, transitions: list[Transition]) -> None:
        if not transitions:
            return
        payload = _build_payload(transitions)
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
                response = await client.post(self._webhook_url, json=payload.model_dump(mode="json"))
        except Exception:
            if self._raise_on_error:
                raise
            _log.exception("slack notify failed")
            return

        if response.status_code >= 400:
            msg = f"slack webhook returned {response.status_code}: {response.text[:200]}"
            if self._raise_on_error:
                raise RuntimeError(msg)
            _log.warning(msg)


def _color_for(transition: Transition) -> str:
    if transition.kind == "recovery":
        return _COLOR_BY_STATUS[Status.OK]
    return _COLOR_BY_STATUS.get(transition.current_status, _COLOR_BY_STATUS[Status.FAIL])


def _build_payload(transitions: list[Transition]) -> SlackPayload:
    failures = [t for t in transitions if t.kind == "failure"]
    recoveries = [t for t in transitions if t.kind == "recovery"]
    summary = f"chap-checker: {len(failures)} new failure(s), {len(recoveries)} recovery"

    header_blocks: list[SlackBlock] = [
        SlackBlock(type="header", text=SlackBlockText(type="plain_text", text=summary)),
    ]

    attachments: list[SlackAttachment] = []
    for t in transitions:
        label = "FAILURE" if t.kind == "failure" else "RECOVERY"
        status_label = t.current_status.value.upper()
        body = f"*{label} — `{t.target_name}`*\n{t.target_url}\n`{t.check_name}`  *{status_label}*  {t.message}"
        attachments.append(
            SlackAttachment(
                color=_color_for(t),
                blocks=[SlackBlock(type="section", text=SlackBlockText(type="mrkdwn", text=body))],
            )
        )

    return SlackPayload(text=summary, blocks=header_blocks, attachments=attachments)
