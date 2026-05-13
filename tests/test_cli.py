from typer.testing import CliRunner

from chap_checker import __version__
from chap_checker.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_global_flags() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--debug" in result.stdout
    assert "--json" in result.stdout
    assert "--quiet" in result.stdout


def test_alert_test_subcommand_registered() -> None:
    result = runner.invoke(app, ["alert", "--help"])
    assert result.exit_code == 0
    assert "test" in result.stdout


def test_verify_help_lists_credentials() -> None:
    result = runner.invoke(app, ["verify", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.stdout
    assert "--username" in result.stdout
    assert "--password" in result.stdout
    assert "--config" in result.stdout
    assert "--instance" in result.stdout
