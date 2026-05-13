"""Thin HTTP client for a DHIS2 server, used by checks."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import BaseModel, Field, HttpUrl

from chap_checker.logging import get_logger

_log = get_logger("client")


class Dhis2Target(BaseModel):
    """The DHIS2 instance under test."""

    base_url: HttpUrl
    username: str
    password: str = Field(repr=False)
    timeout_s: float = 10.0
    verify_tls: bool = True

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
        self._client = httpx.AsyncClient(
            auth=(target.username, target.password),
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
            **kwargs: Forwarded to :meth:`httpx.AsyncClient.get`.
        """
        url = self._target.api_url(path)
        _log.debug("GET %s", url)
        return await self._client.get(url, **kwargs)
