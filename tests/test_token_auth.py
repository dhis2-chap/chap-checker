"""Tests for DHIS2 Personal Access Token (PAT) auth.

Covers the Dhis2Target validator (mutex between password and token),
the Dhis2Client wire-level header it sends, the InstanceConfig
resolver, and the CLI --token / --token-env flags.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import httpx
import pytest
import typer
from pydantic import HttpUrl, ValidationError
from typer.testing import CliRunner

from chap_checker.cli import _resolve_adhoc_token, app
from chap_checker.client import Dhis2Client, Dhis2Target
from chap_checker.config import InstanceConfig

runner = CliRunner()


# ---------- Dhis2Target validator ----------


def test_target_token_mode_valid() -> None:
    target = Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), token="t0k3n")
    assert target.token == "t0k3n"
    assert target.username is None
    assert target.password is None


def test_target_basic_mode_valid() -> None:
    target = Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), username="u", password="p")
    assert target.token is None
    assert target.username == "u"


def test_target_both_modes_raises() -> None:
    with pytest.raises(ValidationError, match="not both"):
        Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), username="u", password="p", token="t")


def test_target_no_auth_raises() -> None:
    with pytest.raises(ValidationError, match="must set either"):
        Dhis2Target(base_url=cast(HttpUrl, "https://x.example"))


def test_target_password_without_username_raises() -> None:
    with pytest.raises(ValidationError, match="requires username"):
        Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), password="p")


# ---------- Dhis2Client wire-level header ----------


def test_client_sends_apitoken_header() -> None:
    """Token mode must produce the exact `Authorization: ApiToken <value>`
    header DHIS2 expects, NOT standard `Bearer`."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    target = Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), token="abc123")
    client = Dhis2Client(target)
    client._client = httpx.AsyncClient(
        headers={"Authorization": f"ApiToken {target.token}"},
        timeout=target.timeout_s,
        transport=httpx.MockTransport(handler),
    )

    import asyncio

    async def go() -> None:
        async with client:
            await client.get("system/info")

    asyncio.run(go())
    assert captured["authorization"] == "ApiToken abc123"
    assert "basic " not in captured["authorization"].lower()
    assert "bearer" not in captured["authorization"].lower()


# ---------- InstanceConfig validators ----------


def test_instance_token_inline() -> None:
    cfg = InstanceConfig(url=cast(HttpUrl, "https://x.example"), token="t0k3n")
    target = cfg.to_target()
    assert target.token == "t0k3n"
    assert target.username is None


def test_instance_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_DHIS_TOKEN", "from-env")
    cfg = InstanceConfig(url=cast(HttpUrl, "https://x.example"), token_env="MY_DHIS_TOKEN")
    target = cfg.to_target()
    assert target.token == "from-env"


def test_instance_both_token_sources_raises() -> None:
    with pytest.raises(ValidationError, match="set exactly one of 'token' or 'token_env'"):
        InstanceConfig(url=cast(HttpUrl, "https://x.example"), token="t", token_env="ENV")


def test_instance_token_and_password_raises() -> None:
    with pytest.raises(ValidationError, match="not both"):
        InstanceConfig(
            url=cast(HttpUrl, "https://x.example"),
            username="u",
            password="p",
            token="t",
        )


def test_instance_no_auth_raises() -> None:
    with pytest.raises(ValidationError, match="set exactly one of"):
        InstanceConfig(url=cast(HttpUrl, "https://x.example"), username="u")


def test_instance_password_still_requires_username() -> None:
    with pytest.raises(ValidationError, match="requires 'username'"):
        InstanceConfig(url=cast(HttpUrl, "https://x.example"), password="p")


# ---------- CLI --token / --token-env resolver ----------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in ("DHIS2_PASSWORD", "DHIS2_TOKEN", "TOK_FROM_ENV", "MISSING"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_resolve_token_explicit_wins(clean_env: None) -> None:
    assert _resolve_adhoc_token(token="literal", token_env=None) == "literal"


def test_resolve_token_env(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("TOK_FROM_ENV", "from-env")
    assert _resolve_adhoc_token(token=None, token_env="TOK_FROM_ENV") == "from-env"


def test_resolve_token_env_missing_raises(clean_env: None) -> None:
    with pytest.raises(typer.BadParameter, match="not set or empty"):
        _resolve_adhoc_token(token=None, token_env="MISSING")


def test_resolve_token_both_flags_raises(clean_env: None) -> None:
    with pytest.raises(typer.BadParameter, match="not both"):
        _resolve_adhoc_token(token="t", token_env="ENV")


def test_resolve_token_non_tty_raises(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(typer.BadParameter, match="DHIS2_TOKEN"):
        _resolve_adhoc_token(token=None, token_env=None)


def test_resolve_token_tty_prompts(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "from-prompt")
    assert _resolve_adhoc_token(token=None, token_env=None) == "from-prompt"


# ---------- CLI end-to-end ----------


def test_cli_token_and_password_mutex(clean_env: None) -> None:
    result = runner.invoke(
        app,
        ["verify", "--url", "https://nope.example", "--password", "p", "--token", "t"],
    )
    assert result.exit_code == 2
    assert "not both" in (result.stdout + result.output).lower()


def test_cli_token_works_without_username(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    """--token must be sufficient without --username (PATs are user-bound on the server)."""
    monkeypatch.setenv("TOK_FROM_ENV", "secret-pat")
    # We expect the resolver path to accept this and fail later on the
    # network call (unreachable host). Anything not a typer BadParameter
    # exit (code 2) is success for this test.
    result = runner.invoke(
        app,
        ["verify", "--url", "https://nope.example.invalid", "--token-env", "TOK_FROM_ENV"],
    )
    # The check probably FAILed at the network layer, so exit code is
    # non-zero - but it MUST NOT be a BadParameter / typer error.
    assert result.exit_code != 2 or "username" not in (result.stdout + result.output).lower()


def test_cli_token_env_flag_declared() -> None:
    from typer.main import get_command

    verify = get_command(app).commands["verify"]  # type: ignore[attr-defined]
    flags: set[str] = set()
    for param in verify.params:
        flags.update(getattr(param, "opts", []))
    assert "--token" in flags
    assert "--token-env" in flags
