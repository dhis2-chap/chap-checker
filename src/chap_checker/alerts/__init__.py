"""Alerter implementations.

Importing this package triggers each alerter module, which registers
itself via :func:`chap_checker.alerts.base.register_alerter` - parallel
to how `chap_checker.checks.__init__` auto-registers built-in checks.

After loading the concrete alerters we patch each class's
`config_model` ClassVar with its `[alerts.<name>]` TOML schema from
`chap_checker.config`. Doing it here avoids a circular import:
`config` consumes `Status` from `checks.base` and is loaded before
alerts; the patch is one-shot and idempotent.
"""

from chap_checker.alerts import slack, webhook
from chap_checker.alerts.base import (
    Alerter,
    AlerterBinding,
    Transition,
    TransitionKind,
    alerter_class,
    all_alerter_classes,
    register_alerter,
)
from chap_checker.alerts.slack import SlackAlerter
from chap_checker.alerts.webhook import WebhookAlerter
from chap_checker.config import SlackAlertConfig, WebhookAlertConfig

SlackAlerter.config_model = SlackAlertConfig
WebhookAlerter.config_model = WebhookAlertConfig

__all__ = [
    "Alerter",
    "AlerterBinding",
    "SlackAlerter",
    "Transition",
    "TransitionKind",
    "WebhookAlerter",
    "alerter_class",
    "all_alerter_classes",
    "register_alerter",
    "slack",
    "webhook",
]
