"""Generic HTTP-webhook alerter.

Posts a canonical JSON envelope to any URL that accepts an
`application/json` POST. Doubles as the base class for transport-specific
alerters (Slack, Teams, PagerDuty Events, ...) - subclasses override
`_build_payload` to swap the body shape while keeping the HTTP plumbing,
timeout, header handling, and error contract on the base.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from pydantic import BaseModel

from chap_checker import __version__
from chap_checker.alerts.base import Transition, register_alerter
from chap_checker.logging import get_logger

if TYPE_CHECKING:
    pass

_log = get_logger("alerts.webhook")


@register_alerter("webhook")
class WebhookAlerter:
    """POST a JSON envelope of transitions to an arbitrary URL.

    The default `_build_payload` emits a canonical shape that's stable
    across versions:

        {
          "checker_version": "<package version>",
          "summary": {"failures": N, "recoveries": M},
          "transitions": [<Transition.model_dump(mode='json')>, ...]
        }

    Receivers can parse it however they like. Users who need a different
    shape (Slack Block Kit, Teams Adaptive Card, PagerDuty Events) subclass
    this and override `_build_payload`.

    Raises on transport errors or non-2xx responses. The CLI dispatcher
    catches the failure, logs it, and skips saving state so the transitions
    are retried on the next run.
    """

    name: ClassVar[str] = "webhook"
    description: ClassVar[str] = (
        "Generic HTTP POST with a canonical JSON envelope (any URL that accepts application/json)."
    )
    # Hand-curated copy-paste snippet shown by `chap-checker alerts list`.
    # Inline comments explain each field; the `# url_env =` line is kept
    # commented because exactly one of url / url_env must be set.
    toml_example: ClassVar[str] = """\
[alerts.webhook]
url = "https://example.com/hook"            # POST destination (exactly one of url / url_env)
# url_env = "MY_WEBHOOK_URL"                # Env var name holding the URL (recommended for shared configs)
notify_on = ["fail", "error", "warn"]       # Statuses that fire alerts (any of: ok, warn, fail, error)
timeout_s = 10.0                            # HTTP timeout in seconds (must be > 0)
headers = { "X-Custom-Header" = "value" }   # Optional literal HTTP headers (auth tokens go here for now)
"""
    # Late lookup of the pydantic config model. Populated by
    # `chap_checker.alerts.__init__` after both modules load. Tools that
    # want to introspect the schema programmatically use this; the human
    # `alerts list` view renders `toml_example` instead.
    config_model: ClassVar[type[BaseModel] | None] = None

    def __init__(
        self,
        url: str,
        timeout_s: float = 10.0,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._timeout_s = timeout_s
        self._headers = dict(headers) if headers else {}
        self._transport = transport

    async def notify(self, transitions: list[Transition]) -> None:
        """POST the transitions. No-op when the list is empty."""
        if not transitions:
            return
        payload = self._build_payload(transitions)
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            response = await client.post(self._url, json=payload, headers=self._headers or None)
        if response.status_code >= 400:
            raise RuntimeError(f"{self.name} webhook returned {response.status_code}: {response.text[:200]}")

    def _build_payload(self, transitions: list[Transition]) -> dict[str, Any]:
        """Return the JSON body to POST. Override in subclasses.

        Default shape is the canonical envelope documented on the class.
        Subclasses are free to return any JSON-serializable dict.
        """
        failures = sum(1 for t in transitions if t.kind == "failure")
        recoveries = sum(1 for t in transitions if t.kind == "recovery")
        return {
            "checker_version": __version__,
            "summary": {"failures": failures, "recoveries": recoveries},
            "transitions": [t.model_dump(mode="json") for t in transitions],
        }
