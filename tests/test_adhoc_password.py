"""Tests for the safer ad-hoc credential paths on ``verify``.

The verify command supports four ways to supply an ad-hoc password,
in priority order: ``--password``, ``--password-env NAME``,
``DHIS2_PASSWORD`` env, and a hidden interactive prompt on a TTY.
These tests cover the resolver behaviour and the helpful errors
when nothing resolves.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import typer
from typer.testing import CliRunner

from chap_checker.cli import _resolve_adhoc_password, app

runner = CliRunner()


@pytest.fixture
def clean_password_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip every env var the resolver might pick up so tests start clean."""
    for key in ("DHIS2_PASSWORD", "PW_FROM_ENV", "MISSING_VAR"):
        monkeypatch.delenv(key, raising=False)
    yield


def test_explicit_password_wins(clean_password_env: None) -> None:
    assert _resolve_adhoc_password(password="literal", password_env=None) == "literal"


def test_password_env_reads_named_var(monkeypatch: pytest.MonkeyPatch, clean_password_env: None) -> None:
    monkeypatch.setenv("PW_FROM_ENV", "from-env")
    assert _resolve_adhoc_password(password=None, password_env="PW_FROM_ENV") == "from-env"


def test_password_env_missing_raises(clean_password_env: None) -> None:
    with pytest.raises(typer.BadParameter, match="not set or empty"):
        _resolve_adhoc_password(password=None, password_env="MISSING_VAR")


def test_password_env_empty_raises(monkeypatch: pytest.MonkeyPatch, clean_password_env: None) -> None:
    monkeypatch.setenv("PW_FROM_ENV", "")
    with pytest.raises(typer.BadParameter, match="not set or empty"):
        _resolve_adhoc_password(password=None, password_env="PW_FROM_ENV")


def test_both_password_and_env_raises(clean_password_env: None) -> None:
    with pytest.raises(typer.BadParameter, match="not both"):
        _resolve_adhoc_password(password="literal", password_env="PW_FROM_ENV")


def test_non_tty_missing_password_raises(monkeypatch: pytest.MonkeyPatch, clean_password_env: None) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(typer.BadParameter, match="--password-env"):
        _resolve_adhoc_password(password=None, password_env=None)


def test_tty_prompts_for_password(monkeypatch: pytest.MonkeyPatch, clean_password_env: None) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "from-prompt")
    assert _resolve_adhoc_password(password=None, password_env=None) == "from-prompt"


def test_cli_url_without_password_errors_in_non_tty(
    monkeypatch: pytest.MonkeyPatch,
    clean_password_env: None,
) -> None:
    """End-to-end: --url without any password source must fail with a clean
    error in a non-TTY context (i.e. how cron / CI invokes the command)."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    result = runner.invoke(app, ["verify", "--url", "https://nope.example", "--username", "u"])
    assert result.exit_code == 2
    assert "password" in (result.stdout + result.output).lower()


def test_cli_password_env_flag_is_declared() -> None:
    """Smoke-check that --password-env actually shows up as a flag on verify."""
    from typer.main import get_command

    verify = get_command(app).commands["verify"]  # type: ignore[attr-defined]
    flags: set[str] = set()
    for param in verify.params:
        flags.update(getattr(param, "opts", []))
    assert "--password-env" in flags
