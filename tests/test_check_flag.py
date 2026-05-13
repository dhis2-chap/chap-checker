"""Tests for the ad-hoc `--check` / `--checks` flag on `verify`."""

from typer.main import get_command
from typer.testing import CliRunner

from chap_checker.cli import app

runner = CliRunner()


def test_unknown_check_name_rejected_in_ad_hoc() -> None:
    result = runner.invoke(
        app,
        [
            "verify",
            "--url",
            "https://nope.example",
            "--username",
            "u",
            "--password",
            "p",
            "--check",
            "does_not_exist",
        ],
    )
    # typer exit code 2 for bad parameter.
    assert result.exit_code == 2
    assert "unknown check" in result.stdout.lower() or "unknown check" in result.output.lower()


def test_verify_declares_check_and_checks_flags() -> None:
    """Inspect the Click command tree directly - more robust than asserting on
    typer's Rich-rendered help output, which gets clipped in headless CI."""
    click_app = get_command(app)
    verify = click_app.commands["verify"]  # type: ignore[attr-defined]
    flags: set[str] = set()
    for param in verify.params:
        flags.update(getattr(param, "opts", []))
        flags.update(getattr(param, "secondary_opts", []))
    assert "--check" in flags
    assert "--checks" in flags
