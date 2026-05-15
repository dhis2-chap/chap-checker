"""Thin HTTP client for a DHIS2 server, used by checks.

Wraps :class:`dhis2w_client.Dhis2Client` (the upstream async DHIS2 client)
with ``skip_version_probe=True`` so no implicit ``/api/system/info``
round-trip happens before the first check runs, and uses
:meth:`dhis2w_client.Dhis2Client.get_response` so callers receive the raw
:class:`httpx.Response` instead of having 4xx / 5xx surface as exceptions.
That matches what a health checker needs: 401 on ``/api/me``, 502 from
``/api/routes/<code>/run/...``, and non-JSON content-types are all
*facts to report*, not errors to throw on.

The wrapper preserves the same public surface (:class:`Dhis2Target`,
:meth:`Dhis2Client.get`) the existing checks call, so the swap is
transparent above this module.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx
from dhis2w_client import AuthProvider, BasicAuth, PatAuth
from dhis2w_client import Dhis2Client as _UpstreamClient
from pydantic import BaseModel, Field, HttpUrl, model_validator

from chap_checker.logging import get_logger

_log = get_logger("client")


class Dhis2Target(BaseModel):
    """The DHIS2 instance under test.

    Auth is one of:

    - ``username`` + ``password`` (HTTP Basic)
    - ``token``                  (DHIS2 Personal Access Token, sent as
      ``Authorization: ApiToken <token>``)

    Exactly one auth mode must be configured. ``username`` is allowed
    alongside ``token`` for logging/display only - it's not sent on
    the wire when a token is set.
    """

    base_url: HttpUrl
    username: str | None = None
    password: str | None = Field(default=None, repr=False)
    token: str | None = Field(default=None, repr=False)
    timeout_s: float = 10.0
    verify_tls: bool = True

    @model_validator(mode="after")
    def _exactly_one_auth_mode(self) -> "Dhis2Target":
        if self.password is not None and self.username is None:
            raise ValueError("Dhis2Target: password requires username.")
        has_basic = self.username is not None and self.password is not None
        has_token = self.token is not None
        if has_basic and has_token:
            raise ValueError("Dhis2Target: pass either (username + password) or token, not both.")
        if not has_basic and not has_token:
            raise ValueError("Dhis2Target: must set either (username + password) or token.")
        return self

    def api_url(self, path: str) -> str:
        """Build a fully-qualified DHIS2 API URL.

        Args:
            path: Path under ``/api`` (with or without a leading slash).
        """
        base = str(self.base_url).rstrip("/")
        path = path.lstrip("/")
        return f"{base}/api/{path}"

    def auth_provider(self) -> AuthProvider:
        """Build the dhis2w-client ``AuthProvider`` matching this target's mode."""
        if self.token is not None:
            return PatAuth(token=self.token)
        assert self.username is not None and self.password is not None  # validator
        return BasicAuth(username=self.username, password=self.password)


class Dhis2Client:
    """Async DHIS2 HTTP client used by checks.

    Wraps :class:`dhis2w_client.Dhis2Client` configured for health-check
    semantics: ``skip_version_probe=True`` so opening the connection
    doesn't itself perform DHIS2 API calls (those are checks' jobs),
    and every request goes through ``get_response`` so 4xx / 5xx come
    back as :class:`httpx.Response` for the caller to inspect.
    Use as an async context manager.
    """

    def __init__(self, target: Dhis2Target) -> None:
        self._target = target
        self._inner = _UpstreamClient(
            str(target.base_url).rstrip("/"),
            auth=target.auth_provider(),
            timeout=target.timeout_s,
            verify=target.verify_tls,
            skip_version_probe=True,
        )

    @property
    def target(self) -> Dhis2Target:
        """Return the configured target."""
        return self._target

    async def __aenter__(self) -> Self:
        await self._inner.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._inner.close()

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """GET a DHIS2 API path.

        Args:
            path: Path under ``/api`` (e.g. ``"system/info"``). A leading
                slash is tolerated.
            params: Query-string parameters forwarded to httpx.
            headers: Extra headers merged into the request after the auth
                header. Auth always wins on conflict — pass alternate
                ``Accept`` / ``X-*`` values here without disturbing auth.
        """
        api_path = "/api/" + path.lstrip("/")
        _log.debug("GET %s%s", str(self._target.base_url).rstrip("/"), api_path)
        return await self._inner.get_response(api_path, params=params, extra_headers=headers)
