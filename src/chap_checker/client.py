"""Thin HTTP client for a DHIS2 server, used by checks."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx
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


class Dhis2Client:
    """Async DHIS2 HTTP client.

    Wraps :class:`httpx.AsyncClient` with basic auth, base-URL handling and
    debug logging. Use as an async context manager.
    """

    def __init__(self, target: Dhis2Target) -> None:
        self._target = target
        kwargs: dict[str, Any] = {
            "timeout": target.timeout_s,
            "verify": target.verify_tls,
            "follow_redirects": True,
        }
        if target.token is not None:
            # DHIS2 Personal Access Tokens use the custom `ApiToken`
            # scheme, NOT standard Bearer - the server only recognises
            # this exact header value for PATs.
            kwargs["headers"] = {"Authorization": f"ApiToken {target.token}"}
        else:
            # Validator guarantees username+password are both set when
            # token is None.
            assert target.username is not None
            assert target.password is not None
            kwargs["auth"] = (target.username, target.password)
        self._client = httpx.AsyncClient(**kwargs)

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
            **kwargs: Forwarded to :meth:`httpx.AsyncClient.get`.
        """
        url = self._target.api_url(path)
        _log.debug("GET %s", url)
        return await self._client.get(url, **kwargs)
