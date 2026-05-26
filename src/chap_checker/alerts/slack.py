"""Slack Incoming Webhook alerter.

Subclasses `WebhookAlerter` so the HTTP plumbing, timeout handling, and
error contract come from one place; this module only owns the Slack
Block Kit payload shape.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from chap_checker.alerts.base import Transition, register_alerter
from chap_checker.alerts.webhook import WebhookAlerter
from chap_checker.checks.base import Status

# Avoid circular import at module load; chap_checker.config imports from
# chap_checker.alerts.base. The reference lives behind the ClassVar so
# `alerts list` can pull TOML field names off the model.

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


@register_alerter("slack")
class SlackAlerter(WebhookAlerter):
    """POST a Block Kit message to a Slack Incoming Webhook URL.

    Behaviour identical to a generic `WebhookAlerter` for the transport
    half (POST, timeout, raise on >=400) - this subclass only customises
    the payload shape to use Slack Block Kit + colored attachments.
    """

    name: ClassVar[str] = "slack"
    description: ClassVar[str] = "Slack Incoming Webhook (Block Kit message with colored attachments)."
    toml_example: ClassVar[str] = """\
[alerts.slack]
# webhook_url = "https://hooks.slack.com/services/REPLACE/ME/HERE"  # Inline URL (NOT recommended for shared configs)
webhook_url_env = "SLACK_WEBHOOK_URL"            # Env var holding the URL (recommended)
notify_on = ["fail", "error", "warn"]            # Statuses that fire alerts (any of: ok, warn, fail, error)
timeout_s = 10.0                                  # HTTP timeout in seconds (must be > 0)
"""
    config_model: ClassVar[type[BaseModel] | None] = None

    def __init__(
        self,
        webhook_url: str,
        timeout_s: float = 10.0,
        transport: Any = None,
    ) -> None:
        # `webhook_url` is the historical kwarg name on this class and is
        # the one wired from `SlackAlertConfig.resolve_webhook_url()`; map
        # it onto the base's generic `url` argument.
        super().__init__(url=webhook_url, timeout_s=timeout_s, transport=transport)

    def _build_payload(self, transitions: list[Transition]) -> dict[str, Any]:
        return _build_slack_payload(transitions).model_dump(mode="json")


def _color_for(transition: Transition) -> str:
    if transition.kind == "recovery":
        return _COLOR_BY_STATUS[Status.OK]
    return _COLOR_BY_STATUS.get(transition.current_status, _COLOR_BY_STATUS[Status.FAIL])


def _build_slack_payload(transitions: list[Transition]) -> SlackPayload:
    failures = [t for t in transitions if t.kind == "failure"]
    recoveries = [t for t in transitions if t.kind == "recovery"]
    failure_word = "failure" if len(failures) == 1 else "failures"
    recovery_word = "recovery" if len(recoveries) == 1 else "recoveries"
    summary = f"chap-checker: {len(failures)} new {failure_word}, {len(recoveries)} {recovery_word}"

    header_blocks: list[SlackBlock] = [
        SlackBlock(type="header", text=SlackBlockText(type="plain_text", text=summary)),
    ]

    attachments: list[SlackAttachment] = []
    for t in transitions:
        label = "FAILURE" if t.kind == "failure" else "RECOVERY"
        status_label = t.current_status.value.upper()
        display = t.target_display_name or t.target_name
        body = f"*{label} — `{display}`*\n{t.target_url}\n`{t.check_name}`  *{status_label}*  {t.message}"
        attachments.append(
            SlackAttachment(
                color=_color_for(t),
                blocks=[SlackBlock(type="section", text=SlackBlockText(type="mrkdwn", text=body))],
            )
        )

    return SlackPayload(text=summary, blocks=header_blocks, attachments=attachments)
