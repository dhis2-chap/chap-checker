"""Typer CLI entry point."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from chap_checker import __version__
from chap_checker.alerts.base import AlerterBinding, Transition
from chap_checker.alerts.slack import SlackAlerter
from chap_checker.checks import all_checks
from chap_checker.checks.base import Status
from chap_checker.client import Dhis2Target
from chap_checker.config import (
    DEFAULT_CONFIG_FILENAME,
    AlertsConfig,
    CheckerConfig,
    default_config_path,
    load_config,
)
from chap_checker.logging import configure as configure_logging
from chap_checker.logging import get_logger
from chap_checker.output import render
from chap_checker.runner import RunReport, TargetEntry, VerifyReport, run_targets_sync
from chap_checker.state import GlobalState
from chap_checker.state_store import (
    DEFAULT_STATE_FILENAME,
    compute_transitions,
    load_state,
    save_state,
)

_log = get_logger("cli")


class AlertTestResult(BaseModel):
    """Outcome of sending one synthetic test alert to a single alerter."""

    alerter: str
    ok: bool
    error: str | None = None


class AlertTestReport(BaseModel):
    """Combined result of ``chap-checker alert test`` across all alerters."""

    ok: bool
    results: list[AlertTestResult] = Field(default_factory=list)


app = typer.Typer(
    name="chap-checker",
    help="Run a suite of checks against a DHIS2 server with chap-core / chap route.",
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable verbose debug logging on stderr.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of a Rich table (cron-friendly).",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress stdout entirely; just run checks, dispatch alerts, and exit (cron-friendly).",
    ),
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit."),
) -> None:
    """Global options shared by every subcommand."""
    if version:
        typer.echo(f"chap-checker {__version__}")
        raise typer.Exit()
    configure_logging(debug)
    ctx.obj = GlobalState(debug=debug, json_output=json_output, quiet=quiet)
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
    no_alerts: bool = typer.Option(
        False,
        "--no-alerts",
        "--no-alert",
        help="Skip alert dispatch even if configured.",
    ),
    state: Path | None = typer.Option(
        None,
        "--state",
        help=f"Path to the persisted state file (default: ./{DEFAULT_STATE_FILENAME} next to the config).",
        envvar="CHAP_CHECKER_STATE",
    ),
) -> None:
    """Run all registered checks against one or more DHIS2 instances.

    Resolution order:

    1. ``--url`` (with ``--username`` / ``--password``): ad-hoc, ignores config.
    2. ``--config <path>``: load that file.
    3. ``./chap-checker.toml`` if it exists.

    Without ``--instance``, every instance in the config is checked.
    """
    state_obj = _state(ctx)
    targets, cfg, config_path = _resolve_run_context(
        config=config,
        instance=instance,
        url=url,
        username=username,
        password=password,
        timeout=timeout,
        insecure=insecure,
    )
    started_at = datetime.now(UTC)
    reports = run_targets_sync(targets)
    finished_at = datetime.now(UTC)

    if not no_alerts and cfg is not None and cfg.alerts is not None and config_path is not None:
        state_path = state if state is not None else config_path.parent / DEFAULT_STATE_FILENAME
        _dispatch_alerts(reports, cfg.alerts, state_path)

    verify_report = VerifyReport(
        checker_version=__version__,
        started_at=started_at,
        finished_at=finished_at,
        runs=reports,
    )

    if not state_obj.quiet:
        render(verify_report, json_output=state_obj.json_output)

    raise typer.Exit(0 if verify_report.ok else 1)


alert_app = typer.Typer(
    name="alert",
    help="Inspect or test alert dispatch.",
    no_args_is_help=True,
)


@alert_app.command("test")
def alert_test_command(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help=f"Path to a TOML config (defaults to ./{DEFAULT_CONFIG_FILENAME} if present).",
        envvar="CHAP_CHECKER_CONFIG",
    ),
) -> None:
    """Send a synthetic transition to every configured alerter.

    Verifies the webhook URL works without waiting for a real failure.
    """
    state_obj = _state(ctx)
    config_path = config if config is not None else default_config_path()
    if not config_path.exists():
        raise typer.BadParameter(
            f"No config at {config_path}. Provide --config <path> or create a ./{DEFAULT_CONFIG_FILENAME}.",
        )
    cfg = load_config(config_path)
    if cfg.alerts is None:
        raise typer.BadParameter(f"{config_path} has no [alerts.*] section.")

    alerters = _build_alerters(cfg.alerts)
    if not alerters:
        raise typer.BadParameter("No alerters configured.")

    test_transition = Transition(
        kind="failure",
        target_name="test",
        target_url="https://test.example.com",
        check_name="alert-test",
        previous_status=Status.OK,
        current_status=Status.FAIL,
        message="Test alert from `chap-checker alert test`.",
        duration_ms=0.0,
        occurred_at=datetime.now(UTC),
    )

    chatter = not state_obj.quiet and not state_obj.json_output

    async def _send_all() -> AlertTestReport:
        results: list[AlertTestResult] = []
        for binding in alerters:
            if chatter:
                typer.echo(f"Sending test message via {binding.alerter.name}...")
            try:
                await binding.alerter.notify([test_transition])
                results.append(AlertTestResult(alerter=binding.alerter.name, ok=True))
                if chatter:
                    typer.echo("  OK")
            except Exception as exc:  # noqa: BLE001 - surface every failure as a result
                results.append(AlertTestResult(alerter=binding.alerter.name, ok=False, error=str(exc)))
                if chatter:
                    typer.echo(f"  FAILED: {exc}")
                _log.exception("alerter %s failed", binding.alerter.name)
        return AlertTestReport(ok=all(r.ok for r in results), results=results)

    report = asyncio.run(_send_all())

    if state_obj.json_output and not state_obj.quiet:
        typer.echo(report.model_dump_json(indent=2))

    raise typer.Exit(0 if report.ok else 1)


app.add_typer(alert_app, name="alert")


checks_app = typer.Typer(
    name="checks",
    help="Inspect available checks.",
    no_args_is_help=True,
)


def _checks_list_impl(ctx: typer.Context) -> None:
    """List every registered check with order, prerequisites, and description."""
    state_obj = _state(ctx)
    checks = all_checks()

    if state_obj.quiet:
        return

    if state_obj.json_output:
        payload = [
            {
                "name": c.name,
                "description": c.description,
                "order": c.order,
                "requires": c.requires,
            }
            for c in checks
        ]
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    console = Console()
    table = Table(title="Registered checks")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Order", justify="right", style="dim")
    table.add_column("Requires", style="dim")
    table.add_column("Description", overflow="fold")
    for c in checks:
        table.add_row(c.name, str(c.order), ", ".join(c.requires) or "-", c.description)
    console.print(table)


checks_app.command("list", help="List registered checks.")(_checks_list_impl)
checks_app.command("ls", help="Alias for 'list'.")(_checks_list_impl)

app.add_typer(checks_app, name="checks")


def _resolve_run_context(
    *,
    config: Path | None,
    instance: str | None,
    url: str | None,
    username: str | None,
    password: str | None,
    timeout: float,
    insecure: bool,
) -> tuple[list[TargetEntry], CheckerConfig | None, Path | None]:
    """Build the list of targets to check plus the loaded config (if any)."""
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
        return [TargetEntry(name="ad-hoc", target=target)], None, None

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
        return [entry.to_target_entry(instance)], cfg, config_path

    targets = [entry.to_target_entry(name) for name, entry in cfg.instances.items()]
    return targets, cfg, config_path


def _build_alerters(cfg: AlertsConfig) -> list[AlerterBinding]:
    """Instantiate one :class:`AlerterBinding` per configured ``[alerts.<name>]`` section."""
    out: list[AlerterBinding] = []
    if cfg.slack is not None:
        out.append(
            AlerterBinding(
                alerter=SlackAlerter(
                    webhook_url=cfg.slack.resolve_webhook_url(),
                    timeout_s=cfg.slack.timeout_s,
                ),
                notify_on=set(cfg.slack.notify_on),
            )
        )
    return out


def _dispatch_alerts(
    reports: list[RunReport],
    alerts_cfg: AlertsConfig,
    state_path: Path,
) -> None:
    """Compute transitions, notify alerters, then persist state (only if delivery succeeded).

    Delivery failures (Slack 5xx, transport errors) are caught so they cannot
    change the run's exit code, but on failure the new state is *not* saved.
    Next run will recompute the same transitions and retry. With multiple
    alerters this is conservative (any failure suppresses the save and may
    re-deliver to alerters that already succeeded); per-alerter dedupe is a
    later-day problem.
    """
    bindings = _build_alerters(alerts_cfg)
    if not bindings:
        return

    notify_on_union: set[Status] = set()
    for binding in bindings:
        notify_on_union.update(binding.notify_on)

    now = datetime.now(UTC)
    previous = load_state(state_path)
    transitions, new_state = compute_transitions(previous, reports, notify_on_union, now)

    if not transitions:
        save_state(state_path, new_state)
        return

    async def _send_all() -> bool:
        all_ok = True
        for binding in bindings:
            filtered = [
                t
                for t in transitions
                if t.current_status in binding.notify_on or t.previous_status in binding.notify_on
            ]
            if not filtered:
                continue
            try:
                await binding.alerter.notify(filtered)
            except Exception:  # noqa: BLE001 - alerts must not change exit code
                all_ok = False
                _log.exception("alerter %s failed", binding.alerter.name)
        return all_ok

    delivery_ok = asyncio.run(_send_all())
    if delivery_ok:
        save_state(state_path, new_state)
    else:
        _log.warning("alerter delivery failed; not saving state so transitions will retry on the next run")


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
