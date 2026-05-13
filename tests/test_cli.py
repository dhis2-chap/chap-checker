from typer.main import get_command
from typer.testing import CliRunner

from chap_checker import __version__
from chap_checker.cli import app

runner = CliRunner()


def _flags_on(command_name: str) -> set[str]:
    """Return every option flag string declared on a subcommand of the app.

    Inspecting the Click command tree directly is more robust than asserting
    substrings of typer's Rich-rendered ``--help`` output, which gets column-
    clipped or swallowed in headless CI environments.
    """
    click_app = get_command(app)
    cmd = click_app.commands[command_name]  # type: ignore[attr-defined]
    flags: set[str] = set()
    for param in cmd.params:
        flags.update(getattr(param, "opts", []))
        flags.update(getattr(param, "secondary_opts", []))
    return flags


def _global_flags() -> set[str]:
    """Flags declared on the top-level ``app`` (the root callback)."""
    click_app = get_command(app)
    flags: set[str] = set()
    for param in click_app.params:
        flags.update(getattr(param, "opts", []))
        flags.update(getattr(param, "secondary_opts", []))
    return flags


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_app_declares_global_flags() -> None:
    flags = _global_flags()
    assert {"--debug", "-d", "--json", "--quiet", "-q", "--version", "-v"} <= flags


def test_alerts_subcommand_registered() -> None:
    """``alerts`` is a Typer group with at least ``list`` and ``test``."""
    click_app = get_command(app)
    alerts_group = click_app.commands["alerts"]  # type: ignore[attr-defined]
    subcommands = set(alerts_group.commands.keys())
    assert {"list", "test"} <= subcommands


def test_verify_declares_credential_and_config_flags() -> None:
    flags = _flags_on("verify")
    assert {
        "--url",
        "--username",
        "-u",
        "--password",
        "-p",
        "--config",
        "-c",
        "--instance",
        "-i",
    } <= flags
