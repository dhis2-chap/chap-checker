"""Typer CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from chap_checker import __version__
from chap_checker.client import Dhis2Target
from chap_checker.config import DEFAULT_CONFIG_FILENAME, default_config_path, load_config
from chap_checker.logging import configure as configure_logging
from chap_checker.output import render
from chap_checker.runner import run_targets_sync
from chap_checker.state import GlobalState

app = typer.Typer(
    name="chap-checker",
    help="Run a suite of checks against a DHIS2 server with chap-core / chap route.",
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Enable verbose debug logging on stderr."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of a Rich table (cron-friendly).",
    ),
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit."),
) -> None:
    """Global options shared by every subcommand."""
    if version:
        typer.echo(f"chap-checker {__version__}")
        raise typer.Exit()
    configure_logging(debug)
    ctx.obj = GlobalState(debug=debug, json_output=json_output)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("verify")
def verify_command(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help=f"Path to a TOML config (defaults to ./{DEFAULT_CONFIG_FILENAME} if present).",
        envvar="CHAP_CHECKER_CONFIG",
    ),
    instance: str | None = typer.Option(
        None,
        "--instance",
        "-i",
        help="Run only this named instance from the config.",
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Ad-hoc DHIS2 base URL (bypasses config).",
        envvar="DHIS2_URL",
    ),
    username: str | None = typer.Option(
        None, "--username", "-u", help="DHIS2 username (ad-hoc mode).", envvar="DHIS2_USERNAME"
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="DHIS2 password (ad-hoc mode).",
        envvar="DHIS2_PASSWORD",
    ),
    timeout: float = typer.Option(10.0, "--timeout", help="HTTP timeout per request (seconds, ad-hoc mode)."),
    insecure: bool = typer.Option(False, "--insecure", help="Skip TLS certificate verification (ad-hoc mode)."),
) -> None:
    """Run all registered checks against one or more DHIS2 instances.

    Resolution order:

    1. ``--url`` (with ``--username`` / ``--password``): ad-hoc, ignores config.
    2. ``--config <path>``: load that file.
    3. ``./chap-checker.toml`` if it exists.

    Without ``--instance``, every instance in the config is checked.
    """
    state = _state(ctx)
    targets = _resolve_targets(
        config=config,
        instance=instance,
        url=url,
        username=username,
        password=password,
        timeout=timeout,
        insecure=insecure,
    )
    reports = run_targets_sync(targets)
    render(reports, json_output=state.json_output)

    exit_code = 0 if all(r.ok for r in reports) else 1
    raise typer.Exit(exit_code)


def _resolve_targets(
    *,
    config: Path | None,
    instance: str | None,
    url: str | None,
    username: str | None,
    password: str | None,
    timeout: float,
    insecure: bool,
) -> list[tuple[str, Dhis2Target]]:
    if url is not None:
        if instance is not None:
            raise typer.BadParameter("--instance cannot be combined with --url; --url is ad-hoc mode.")
        if username is None or password is None:
            raise typer.BadParameter("--url requires --username and --password.")
        target = Dhis2Target(
            base_url=url,  # type: ignore[arg-type]  # Pydantic coerces str -> HttpUrl
            username=username,
            password=password,
            timeout_s=timeout,
            verify_tls=not insecure,
        )
        return [("ad-hoc", target)]

    config_path = config if config is not None else default_config_path()
    if not config_path.exists():
        raise typer.BadParameter(
            f"No config at {config_path}. Provide --config <path>, "
            f"create a ./{DEFAULT_CONFIG_FILENAME}, or use --url for ad-hoc mode.",
        )
    cfg = load_config(config_path)
    if not cfg.instances:
        raise typer.BadParameter(f"{config_path} contains no [instances.<name>] entries.")

    if instance is not None:
        try:
            entry = cfg.get(instance)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        return [(instance, entry.to_target())]

    return [(name, entry.to_target()) for name, entry in cfg.instances.items()]


def _state(ctx: typer.Context) -> GlobalState:
    obj = ctx.obj
    if isinstance(obj, GlobalState):
        return obj
    return GlobalState()


def main() -> None:
    """Entry point used by the ``chap-checker`` console script."""
    app()


if __name__ == "__main__":
    main()
