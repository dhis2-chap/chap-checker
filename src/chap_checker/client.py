"""Thin HTTP client for a DHIS2 server, used by checks.

Wraps :class:`httpx.AsyncClient` for transport and delegates Authorization
header generation to :mod:`dhis2w_client.v42.auth` providers so chap-checker
shares the auth abstraction with the rest of the dhis2w toolkit. The full
``dhis2w_client.Dhis2Client`` isn't used here: its ``connect()`` does a
version-probe round-trip and raises on 4xx, both of which are states this
tool needs to *report* on rather than throw on.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx
from dhis2w_client import AuthProvider, BasicAuth, PatAuth
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
    """Async DHIS2 HTTP client.

    Wraps :class:`httpx.AsyncClient` with auth, base-URL handling and debug
    logging. Authorization headers come from the
    :class:`dhis2w_client.AuthProvider` built from the target, so swapping
    between Basic / PAT / (future) OAuth2 happens upstream of this class.
    Use as an async context manager.
    """

    def __init__(self, target: Dhis2Target) -> None:
        self._target = target
        self._auth: AuthProvider = target.auth_provider()
        self._client = httpx.AsyncClient(
            timeout=target.timeout_s,
            verify=target.verify_tls,
            follow_redirects=True,
        )

    @property
    def target(self) -> Dhis2Target:
        """Return the configured target."""
        return self._target

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """GET a DHIS2 API path.

        Args:
            path: Path under ``/api`` (e.g. ``"system/info"``).
            **kwargs: Forwarded to :meth:`httpx.AsyncClient.get`. A
                ``headers`` mapping is merged on top of the auth header
                produced by the configured :class:`AuthProvider`, so
                callers can add ``Accept`` / ``X-*`` without dropping
                auth.
        """
        url = self._target.api_url(path)
        auth_headers = await self._auth.headers()
        caller_headers = kwargs.pop("headers", None) or {}
        headers = {**auth_headers, **caller_headers}
        _log.debug("GET %s", url)
        return await self._client.get(url, headers=headers, **kwargs)
