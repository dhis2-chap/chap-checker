"""Configuration model + factory for the DHIS2 client checks use.

chap-checker doesn't ship its own DHIS2 client anymore — it uses
:class:`dhis2w_client.Dhis2Client` directly. This module's job is to
turn a :class:`Dhis2Target` (the validated config a CLI or a TOML
section produces) into a configured upstream client via
:meth:`Dhis2Target.open`. Checks then call ``client.get_response(...)``
exactly like every other dhis2w-client consumer.
"""

from __future__ import annotations

from dhis2w_client import AuthProvider, BasicAuth, Dhis2Client, PatAuth
from pydantic import BaseModel, Field, HttpUrl, model_validator


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

    def _auth_provider(self) -> AuthProvider:
        """Build the dhis2w-client ``AuthProvider`` matching this target's mode."""
        if self.token is not None:
            return PatAuth(token=self.token)
        assert self.username is not None and self.password is not None  # validator
        return BasicAuth(username=self.username, password=self.password)

    def open(self) -> Dhis2Client:
        """Return a configured upstream client; use as ``async with target.open() as client:``.

        ``skip_version_probe=True`` keeps ``connect()`` from doing the
        canonical-URL + ``/api/system/info`` round-trips — those probes
        are exactly the states a health-checker wants to *observe* via a
        check, not have raised inside the context-manager opening.
        Callers reach for ``client.get_response(path)`` (returns the raw
        ``httpx.Response`` without raising on 4xx/5xx) rather than the
        typed accessors.
        """
        return Dhis2Client(
            str(self.base_url).rstrip("/"),
            auth=self._auth_provider(),
            timeout=self.timeout_s,
            verify=self.verify_tls,
            skip_version_probe=True,
        )
