"""Tests for DHIS2 Personal Access Token (PAT) auth.

Covers the Dhis2Target validator (mutex between password and token),
the wire-level Authorization header the upstream `dhis2w_client` sends
when the target opens a client, the InstanceConfig resolver, and the
CLI --token / --token-env flags.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import httpx2 as httpx
import pytest
import typer
from pydantic import HttpUrl, ValidationError
from typer.testing import CliRunner

from chap_checker.cli import _resolve_adhoc_password, _resolve_adhoc_token, app
from chap_checker.client import Dhis2Target
from chap_checker.config import Dhis2InstanceConfig
from chap_checker.runner import RunReport, TargetEntry

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


# ---------- Wire-level Authorization header ----------


def test_client_sends_apitoken_header() -> None:
    """Token mode must produce the exact `Authorization: ApiToken <value>`
    header DHIS2 expects, NOT standard `Bearer`."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    target = Dhis2Target(base_url=cast(HttpUrl, "https://x.example"), token="abc123")
    client = target.open()
    # The auth header is built by dhis2w_client.PatAuth on every request,
    # so the MockTransport-backed AsyncClient does not need to set it.
    client._http = httpx.AsyncClient(
        base_url=str(target.base_url).rstrip("/"),
        timeout=target.timeout_s,
        transport=httpx.MockTransport(handler),
    )

    import asyncio

    async def go() -> None:
        async with client:
            await client.get_response("/api/system/info")

    asyncio.run(go())
    assert captured["authorization"] == "ApiToken abc123"
    assert "basic " not in captured["authorization"].lower()
    assert "bearer" not in captured["authorization"].lower()


# ---------- InstanceConfig validators ----------


def test_instance_token_inline() -> None:
    cfg = Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), token="t0k3n")
    target = cfg.to_target()
    assert target.token == "t0k3n"
    assert target.username is None


def test_instance_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_DHIS_TOKEN", "from-env")
    cfg = Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), token_env="MY_DHIS_TOKEN")
    target = cfg.to_target()
    assert target.token == "from-env"


def test_instance_both_token_sources_raises() -> None:
    with pytest.raises(ValidationError, match="set exactly one of 'token' or 'token_env'"):
        Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), token="t", token_env="ENV")


def test_instance_token_and_password_raises() -> None:
    with pytest.raises(ValidationError, match="not both"):
        Dhis2InstanceConfig(
            url=cast(HttpUrl, "https://x.example"),
            username="u",
            password="p",
            token="t",
        )


def test_instance_no_auth_raises() -> None:
    with pytest.raises(ValidationError, match="set exactly one of"):
        Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), username="u")


def test_instance_password_still_requires_username() -> None:
    with pytest.raises(ValidationError, match="requires 'username'"):
        Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), password="p")


# ---------- CLI --token / --token-env resolver ----------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in (
        "DHIS2_PASSWORD",
        "DHIS2_TOKEN",
        "DHIS2_USERNAME",
        "DHIS2_URL",
        "CHAP_CHECKER_TOKEN",
        "CHAP_CHECKER_CONFIG",
        "TOK_FROM_ENV",
        "PW_FROM_ENV",
        "MISSING",
    ):
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


def test_resolve_token_env_beats_ambient_token(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    """A `--token` value filled from `DHIS2_TOKEN` must not conflict with `--token-env`."""
    monkeypatch.setenv("TOK_FROM_ENV", "from-env")
    assert _resolve_adhoc_token(token="ambient", token_env="TOK_FROM_ENV", token_explicit=False) == "from-env"


def test_resolve_password_env_beats_ambient_password(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    """A `--password` value filled from `DHIS2_PASSWORD` must not conflict with `--password-env`."""
    monkeypatch.setenv("PW_FROM_ENV", "from-env")
    resolved = _resolve_adhoc_password(password="ambient", password_env="PW_FROM_ENV", password_explicit=False)
    assert resolved == "from-env"


def test_resolve_token_non_tty_raises(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(typer.BadParameter, match="DHIS2_TOKEN"):
        _resolve_adhoc_token(token=None, token_env=None)


def test_resolve_token_tty_prompts(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "from-prompt")
    assert _resolve_adhoc_token(token=None, token_env=None) == "from-prompt"


# ---------- CLI end-to-end ----------
#
# `verify` is driven through the real Typer app, but the check runner
# is stubbed so no network call happens. The stub records the targets
# the CLI built, which lets each test assert the selected auth mode and
# the exact credential instead of inferring it from the exit code.


@pytest.fixture
def captured_targets(monkeypatch: pytest.MonkeyPatch) -> list[TargetEntry]:
    captured: list[TargetEntry] = []

    def fake_run_targets_sync(targets: list[TargetEntry], **_kwargs: Any) -> list[RunReport]:
        captured.extend(targets)
        return []

    monkeypatch.setattr("chap_checker.cli.run_targets_sync", fake_run_targets_sync)
    # Never fall into an interactive prompt from inside CliRunner.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return captured


def _only_dhis2_target(captured: list[TargetEntry]) -> Dhis2Target:
    assert len(captured) == 1
    target = captured[0].target
    assert isinstance(target, Dhis2Target)
    return target


URL = "https://nope.example.invalid"


def test_cli_token_and_password_mutex(clean_env: None, captured_targets: list[TargetEntry]) -> None:
    result = runner.invoke(app, ["verify", "--url", URL, "--password", "p", "--token", "t"])
    assert result.exit_code == 2
    assert "not both" in result.output.lower()
    assert captured_targets == []


def test_cli_token_works_without_username(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """--token-env must be sufficient without --username (PATs are user-bound on the server)."""
    monkeypatch.setenv("TOK_FROM_ENV", "secret-pat")
    result = runner.invoke(app, ["verify", "--url", URL, "--token-env", "TOK_FROM_ENV"])
    assert result.exit_code == 0, result.output
    target = _only_dhis2_target(captured_targets)
    assert target.token == "secret-pat"
    assert target.username is None
    assert target.password is None


def test_cli_token_env_flag_declared() -> None:
    from typer.main import get_command

    verify = get_command(app).commands["verify"]  # type: ignore[attr-defined]
    flags: set[str] = set()
    for param in verify.params:
        flags.update(getattr(param, "opts", []))
    assert "--token" in flags
    assert "--token-env" in flags


# ---------- Regression: ambient env vars must not trigger the mutex ----------


def test_dhis2_password_in_env_does_not_block_token_env_flag(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """`--token-env FOO` with ambient `DHIS2_PASSWORD` must select token mode."""
    monkeypatch.setenv("DHIS2_PASSWORD", "ambient-pw")
    monkeypatch.setenv("TOK_FROM_ENV", "the-token")
    result = runner.invoke(app, ["verify", "--url", URL, "--token-env", "TOK_FROM_ENV"])
    assert result.exit_code == 0, result.output
    target = _only_dhis2_target(captured_targets)
    assert target.token == "the-token"
    assert target.password is None


def test_dhis2_token_in_env_does_not_block_password_env_flag(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """Symmetric: `--password-env FOO` + ambient `DHIS2_TOKEN` must select basic auth."""
    monkeypatch.setenv("DHIS2_TOKEN", "ambient-token")
    monkeypatch.setenv("PW_FROM_ENV", "the-pw")
    result = runner.invoke(app, ["verify", "--url", URL, "--username", "u", "--password-env", "PW_FROM_ENV"])
    assert result.exit_code == 0, result.output
    target = _only_dhis2_target(captured_targets)
    assert target.token is None
    assert target.username == "u"
    assert target.password == "the-pw"


def test_token_env_beats_ambient_dhis2_token(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """`--token-env OTHER` with `DHIS2_TOKEN` exported must use OTHER, not error."""
    monkeypatch.setenv("DHIS2_TOKEN", "ambient-token")
    monkeypatch.setenv("TOK_FROM_ENV", "explicit-token")
    result = runner.invoke(app, ["verify", "--url", URL, "--token-env", "TOK_FROM_ENV"])
    assert result.exit_code == 0, result.output
    assert _only_dhis2_target(captured_targets).token == "explicit-token"


def test_password_env_beats_ambient_dhis2_password(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """`--password-env OTHER` with `DHIS2_PASSWORD` exported must use OTHER, not error."""
    monkeypatch.setenv("DHIS2_PASSWORD", "ambient-pw")
    monkeypatch.setenv("PW_FROM_ENV", "explicit-pw")
    result = runner.invoke(app, ["verify", "--url", URL, "--username", "u", "--password-env", "PW_FROM_ENV"])
    assert result.exit_code == 0, result.output
    target = _only_dhis2_target(captured_targets)
    assert target.password == "explicit-pw"
    assert target.token is None


def test_explicit_token_and_token_env_still_conflict(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    monkeypatch.setenv("TOK_FROM_ENV", "explicit-token")
    result = runner.invoke(app, ["verify", "--url", URL, "--token", "t", "--token-env", "TOK_FROM_ENV"])
    assert result.exit_code == 2
    assert "not both" in result.output.lower()
    assert captured_targets == []


def test_explicit_password_and_password_env_still_conflict(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    monkeypatch.setenv("PW_FROM_ENV", "explicit-pw")
    result = runner.invoke(
        app,
        ["verify", "--url", URL, "--username", "u", "--password", "p", "--password-env", "PW_FROM_ENV"],
    )
    assert result.exit_code == 2
    assert "not both" in result.output.lower()
    assert captured_targets == []


def test_ambient_dhis2_token_alone_selects_token_mode(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    monkeypatch.setenv("DHIS2_TOKEN", "ambient-token")
    result = runner.invoke(app, ["verify", "--url", URL])
    assert result.exit_code == 0, result.output
    assert _only_dhis2_target(captured_targets).token == "ambient-token"


def test_both_env_vars_ambient_with_no_flags_errors_clearly(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """If only env vars are set and they collide, error with a helpful hint.

    No CLI flags - both DHIS2_TOKEN and DHIS2_PASSWORD exported. We
    can't infer intent, so error out instead of silently picking one.
    """
    monkeypatch.setenv("DHIS2_TOKEN", "t")
    monkeypatch.setenv("DHIS2_PASSWORD", "p")
    result = runner.invoke(app, ["verify", "--url", URL])
    assert result.exit_code == 2
    out = result.output.lower()
    assert "dhis2_token" in out and "dhis2_password" in out
    assert captured_targets == []


def test_explicit_username_forces_password_mode_even_with_dhis2_token_env(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_targets: list[TargetEntry],
) -> None:
    """`--username u` is an explicit "I want basic auth" signal.

    Even with `DHIS2_TOKEN` exported, the resolver must not flip to
    token mode behind the user's back; the password comes from the
    ambient `DHIS2_PASSWORD`.
    """
    monkeypatch.setenv("DHIS2_TOKEN", "ambient-token")
    monkeypatch.setenv("DHIS2_PASSWORD", "ambient-pw")
    result = runner.invoke(app, ["verify", "--url", URL, "--username", "u"])
    assert result.exit_code == 0, result.output
    target = _only_dhis2_target(captured_targets)
    assert target.token is None
    assert target.username == "u"
    assert target.password == "ambient-pw"


# ---------- tui --connect token flags ----------


@pytest.fixture
def captured_dashboard(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("chap_checker.dashboard.run", fake_run)
    return captured


def test_tui_token_env_beats_ambient_chap_checker_token(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_dashboard: dict[str, Any],
) -> None:
    monkeypatch.setenv("CHAP_CHECKER_TOKEN", "ambient")
    monkeypatch.setenv("TOK_FROM_ENV", "explicit")
    result = runner.invoke(app, ["tui", "--connect", "http://tv:8765", "--token-env", "TOK_FROM_ENV"])
    assert result.exit_code == 0, result.output
    assert captured_dashboard["connect_token"] == "explicit"


def test_tui_ambient_chap_checker_token_used_without_flags(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_dashboard: dict[str, Any],
) -> None:
    monkeypatch.setenv("CHAP_CHECKER_TOKEN", "ambient")
    result = runner.invoke(app, ["tui", "--connect", "http://tv:8765"])
    assert result.exit_code == 0, result.output
    assert captured_dashboard["connect_token"] == "ambient"


def test_tui_explicit_token_and_token_env_conflict(
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    captured_dashboard: dict[str, Any],
) -> None:
    monkeypatch.setenv("TOK_FROM_ENV", "explicit")
    result = runner.invoke(
        app,
        ["tui", "--connect", "http://tv:8765", "--token", "t", "--token-env", "TOK_FROM_ENV"],
    )
    assert result.exit_code == 2
    assert "not both" in result.output.lower()
    assert captured_dashboard == {}
