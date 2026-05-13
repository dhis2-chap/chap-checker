"""Tests for the ad-hoc `--check` / `--checks` flag on `verify`."""

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


def test_verify_help_lists_check_flag() -> None:
    result = runner.invoke(app, ["verify", "--help"])
    assert result.exit_code == 0
    assert "--check" in result.stdout
    assert "--checks" in result.stdout
