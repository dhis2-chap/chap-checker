"""Configuration models + client factories for the targets checks run against.

A target is one server under test. Every target kind shares
:class:`BaseTarget` (URL, timeout, TLS verification) and knows how to open
the client its checks expect:

- :class:`Dhis2Target` (``kind = "dhis2"``) opens a
  :class:`dhis2w_client.Dhis2Client`. Checks call
  ``client.get_response(...)`` exactly like every other dhis2w-client
  consumer.
- :class:`OcsTarget` (``kind = "ocs"``) opens a plain ``httpx2.AsyncClient``
  rooted at an Open Climate Service deployment. OCS has no auth, so the
  client carries no credentials.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import httpx2 as httpx
from dhis2w_client import AuthProvider, BasicAuth, Dhis2, Dhis2Client, PatAuth, RetryPolicy
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

TargetKind = Literal["dhis2", "ocs"]

# Human-readable platform label per kind, shown on dashboard tiles.
PLATFORM_LABELS: dict[str, str] = {"dhis2": "DHIS2", "ocs": "OCS"}


class BaseTarget(BaseModel):
    """Fields every target kind shares.

    ``kind`` is a class-level constant on each subclass; it picks which
    checks apply (see :func:`chap_checker.checks.base.check_kinds`) and
    which client :meth:`open` returns.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: ClassVar[TargetKind]

    base_url: HttpUrl
    timeout_s: float = 10.0
    verify_tls: bool = True

    def open(self) -> Any:
        """Return the async-context-managed client this kind's checks expect."""
        raise NotImplementedError


class OcsTarget(BaseTarget):
    """An Open Climate Service (openEO backend) deployment under test.

    OCS exposes its API without authentication, so there are no
    credential fields. Retries are not supported for this kind yet.
    """

    kind: ClassVar[TargetKind] = "ocs"

    def open(self) -> httpx.AsyncClient:
        """Return an ``httpx2.AsyncClient`` rooted at the deployment; use as ``async with``."""
        return httpx.AsyncClient(
            base_url=str(self.base_url).rstrip("/"),
            timeout=self.timeout_s,
            verify=self.verify_tls,
            follow_redirects=True,
            max_redirects=5,
        )


class Dhis2Target(BaseTarget):
    """The DHIS2 instance under test.

    Auth is one of:

    - ``username`` + ``password`` (HTTP Basic)
    - ``token``                  (DHIS2 Personal Access Token, sent as
      ``Authorization: ApiToken <token>``)

    Exactly one auth mode must be configured. ``username`` is allowed
    alongside ``token`` for logging/display only - it's not sent on
    the wire when a token is set.

    ``retry_policy`` is the optional ``dhis2w_client.RetryPolicy`` to
    install on the upstream client. When set, transient transport
    failures and the configured ``retry_statuses`` (default 429 / 502 /
    503 / 504) are retried with exponential backoff. Off by default
    because a health-checker generally wants to see flakes, not paper
    over them - opt in per-instance or globally via the TOML.
    """

    kind: ClassVar[TargetKind] = "dhis2"

    username: str | None = None
    password: str | None = Field(default=None, repr=False)
    token: str | None = Field(default=None, repr=False)
    retry_policy: RetryPolicy | None = None

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

    def _auth_provider(self) -> AuthProvider:
        """Build the dhis2w-client ``AuthProvider`` matching this target's mode."""
        if self.token is not None:
            return PatAuth(token=self.token)
        assert self.username is not None and self.password is not None  # validator
        return BasicAuth(username=self.username, password=self.password)

    def open(self, *, version: Dhis2 | None = None) -> Dhis2Client:
        """Return a configured upstream client; use as ``async with target.open() as client:``.

        ``skip_version_probe=True`` keeps ``connect()`` from doing the
        canonical-URL + ``/api/system/info`` round-trips — those probes
        are exactly the states a health-checker wants to *observe* via a
        check, not have raised inside the context-manager opening.
        Callers reach for ``client.get_response(path)`` (returns the raw
        ``httpx.Response`` without raising on 4xx/5xx) rather than the
        typed accessors.

        ``version`` pins the generated module the client will use for
        typed resources (`client.system.me()` etc.). Pass the value of
        :attr:`CheckContext.dhis2_version` after `dhis2_system_info` has
        detected it; leave as ``None`` (the default) to keep the
        dhis2w-client default. Status-aware checks using
        ``client.get_response(path)`` are version-agnostic and don't need
        this; opening a fresh version-pinned client per check that wants
        typed access is the recommended pattern.
        """
        kwargs: dict[str, object] = {
            "auth": self._auth_provider(),
            "timeout": self.timeout_s,
            "verify": self.verify_tls,
            "skip_version_probe": True,
        }
        if version is not None:
            kwargs["version"] = version
        if self.retry_policy is not None:
            kwargs["retry_policy"] = self.retry_policy
        return Dhis2Client(str(self.base_url).rstrip("/"), **kwargs)  # type: ignore[arg-type]
