"""Textual TUI dashboard for chap-checker.

One tile per configured instance. Designed to be left on a TV / monitor so
the operator can see at a glance which targets are up, which version they're
running, and which check just failed. Whether alerts dispatch is decided at
launch time via `--alerts` / `--no-alerts`.

Two modes, picked at launch:

- **Local**: embeds a `DashboardServer` (from `chap_checker.daemon`) and
  drives `refresh_once()` from the Textual event loop on each tick. No
  external process required.
- **Connect (`--connect URL`)**: polls `{URL}/api/state` over HTTP each
  tick and renders the response. The TV's `chap-checker serve` becomes
  the single source of truth; the laptop's TUI is a thin client.

Both modes feed the same `DashboardState` view model into the same
rendering path; the only thing that differs is where the snapshot
comes from.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import httpx
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from chap_checker.checks.base import Status
from chap_checker.config import CheckerConfig, load_config
from chap_checker.daemon import (
    HISTORY_LEN,
    DashboardServer,
    DashboardState,
    TileModel,
    extract_dhis2_version,
    worst,
)
from chap_checker.runner import TargetEntry

# Re-exports preserved for the legacy import paths used elsewhere in the
# codebase and in tests. `dashboard.py` was the original home of these
# helpers; both still work, daemon.py is the source of truth.
_extract_dhis2_version = extract_dhis2_version
_worst = worst
_HISTORY_LEN = HISTORY_LEN

# Primary accent. Used inside Static-rendered Rich markup as `[bold $success]…[/]`;
# Textual's markup parser resolves `$success` to the active theme's success
# colour at render time, so swapping themes hot-swaps the colour.
_ACCENT = "$success"

# Theme tokens for the per-cell uptime strip. These names map onto the keys
# of `app.theme_variables` so `_resolve_token(...)` can pull live colours
# at render time (Rich's Style argument needs an actual colour, not a
# `$-prefixed` token — only markup understands those).
_STRIP_TOKEN_BY_STATUS: dict[Status, str] = {
    Status.OK: "success",
    Status.WARN: "warning",
    Status.FAIL: "error",
    Status.ERROR: "error",
    # `text-disabled` is alpha-based (`auto 38%`) and won't work as a Rich
    # `Style` value; use a real surface-tinted hex instead. Both SKIPPED
    # and EMPTY slots intentionally share the same dim placeholder colour
    # because both mean "nothing meaningful happened here".
    Status.SKIPPED: "panel-lighten-3",
}
_STRIP_TOKEN_EMPTY = "surface-lighten-2"


# ---------- Theme definitions ----------
#
# Each [ui].theme value in chap-checker.toml maps to a Textual Theme that
# the dashboard registers on mount and selects via App.theme. Tokens used
# inside the dashboard's TCSS — `$background`, `$surface`, `$success`,
# `$warning`, `$error`, `$text`, `$text-muted`, `$text-disabled` — pull
# their values from the active theme, so changing the toml's [ui].theme
# and reloading is enough to swap the look.


def _phosphor_theme() -> Theme:
    return Theme(
        name="chap-phosphor",
        primary="#7DD345",
        success="#7DD345",
        warning="#d4a017",
        error="#d04040",
        background="#0e0e0e",
        surface="#161616",
        panel="#161616",
        foreground="#dddddd",
        dark=True,
        variables={
            "header-bg": "#0e0e0e",
            "header-brand": "#7DD345",
            "header-meta": "#aaaaaa",
        },
    )


def _amber_theme() -> Theme:
    return Theme(
        name="chap-amber",
        primary="#ffb84d",
        success="#7DD345",
        warning="#ffb84d",
        error="#d04040",
        background="#0a0705",
        surface="#100b07",
        panel="#100b07",
        foreground="#e8d4a8",
        dark=True,
        variables={
            "header-bg": "#0a0705",
            "header-brand": "#ffb84d",
            "header-meta": "#aaaaaa",
        },
    )


def _high_theme() -> Theme:
    return Theme(
        name="chap-high",
        primary="#9eff9e",
        success="#9eff9e",
        warning="#ffd042",
        error="#ff5a5a",
        background="#000000",
        surface="#0c0c0c",
        panel="#0c0c0c",
        foreground="#ffffff",
        dark=True,
        variables={
            "header-bg": "#000000",
            "header-brand": "#9eff9e",
            "header-meta": "#cccccc",
        },
    )


def _tokyo_theme() -> Theme:
    return Theme(
        name="chap-tokyo",
        primary="#7aa2f7",
        success="#9ece6a",
        warning="#e0af68",
        error="#f7768e",
        background="#11131a",
        surface="#161922",
        panel="#161922",
        foreground="#c0caf5",
        dark=True,
        variables={
            "header-bg": "#11131a",
            "header-brand": "#7aa2f7",
            "header-meta": "#aaaaaa",
        },
    )


def _dhis2_theme() -> Theme:
    return Theme(
        name="chap-dhis2",
        primary="#1f4d75",
        success="#2e6b32",
        warning="#b85b00",
        error="#a8302f",
        background="#c5cad0",
        surface="#e3e7eb",
        panel="#e3e7eb",
        foreground="#1e293b",
        dark=False,
        variables={
            "header-bg": "#1f4d75",
            "header-brand": "#ffffff",
            "header-meta": "#cdd5e0",
        },
    )


_THEME_BUILDERS = {
    "phosphor": _phosphor_theme,
    "amber": _amber_theme,
    "high": _high_theme,
    "tokyo": _tokyo_theme,
    "dhis2": _dhis2_theme,
}


def _textual_theme_name(ui_theme: str) -> str:
    """Map a chap-checker [ui].theme value to the registered Textual theme name."""
    builder = _THEME_BUILDERS.get(ui_theme, _phosphor_theme)
    return builder().name


_PILL_CLASS_BY_STATUS = {
    Status.OK: "pill-ok",
    Status.WARN: "pill-warn",
    Status.FAIL: "pill-fail",
    Status.ERROR: "pill-error",
    Status.SKIPPED: "pill-skipped",
}

_SYMBOL_BY_STATUS = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.ERROR: "!!",
    Status.SKIPPED: "·",
}


class UptimeStrip(Widget):
    """One-row coloured-block history strip that fills its widget width.

    Each cell is one terminal column wide; the rightmost cell is the
    most recent refresh and the strip pads with dim placeholders on
    the left until enough history accumulates. Reads `self.size`
    at render time so the strip spans the full tile width regardless
    of how many instances are configured.
    """

    DEFAULT_CSS = """
    UptimeStrip {
        height: 1;
        width: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, history_provider: Callable[[], list[Status]]) -> None:
        super().__init__()
        self._history_provider = history_provider

    def _resolve(self, token: str) -> str:
        """Pull a live colour from the active theme for the given token name."""
        try:
            return self.app.theme_variables.get(token, "#888888")
        except Exception:  # noqa: BLE001 - fall back to a neutral grey if no app yet
            return "#888888"

    def render(self) -> Text:
        # Padding eats one cell on each side (`padding: 0 1`).
        width = max(1, self.size.width - 2)
        slots = list(self._history_provider())
        text = Text()
        empty_color = self._resolve(_STRIP_TOKEN_EMPTY)
        if width <= len(slots):
            for status in slots[-width:]:
                tok = _STRIP_TOKEN_BY_STATUS.get(status, _STRIP_TOKEN_EMPTY)
                text.append("█", style=self._resolve(tok))
            return text
        text.append("█" * (width - len(slots)), style=empty_color)
        for status in slots:
            tok = _STRIP_TOKEN_BY_STATUS.get(status, _STRIP_TOKEN_EMPTY)
            text.append("█", style=self._resolve(tok))
        return text


def columns_for(n_instances: int) -> int:
    """Pick a column count that looks balanced for `n_instances` tiles."""
    if n_instances <= 1:
        return 1
    if n_instances <= 4:
        return 2
    if n_instances <= 9:
        return 3
    return 4


def _format_relative(now: datetime, then: datetime) -> str:
    """Render `then` as 'Ns ago' / 'Nm ago' / 'Nh ago'."""
    delta = max(0, int((now - then).total_seconds()))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


class DashboardHeader(Horizontal):
    """Custom top bar: brand | N instance(s) | alerts ... | refresh every Ns ... HH:MM:SS."""

    DEFAULT_CSS = """
    DashboardHeader {
        height: 1;
        padding: 0 2;
        background: $header-bg;
    }
    DashboardHeader .hdr-name {
        color: $header-brand;
        text-style: bold;
        width: auto;
    }
    DashboardHeader .hdr-pipe {
        color: $header-meta;
        width: 3;
        content-align: center middle;
    }
    DashboardHeader .hdr-text {
        color: $header-meta;
        width: auto;
    }
    DashboardHeader .hdr-clock {
        color: $header-meta;
        width: 1fr;
        content-align: right middle;
    }
    """

    def __init__(
        self,
        n_instances: int,
        alerts_enabled: bool,
        interval_s: float,
        title: str,
    ) -> None:
        super().__init__()
        self._n = n_instances
        self._alerts = alerts_enabled
        self._interval = interval_s
        self._title = title

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="hdr-name", id="hdr-name")
        yield Static("|", classes="hdr-pipe")
        yield Static(f"{self._n} instance(s)", classes="hdr-text", id="hdr-instances")
        yield Static("|", classes="hdr-pipe")
        yield Static(f"alerts {'ON' if self._alerts else 'OFF'}", classes="hdr-text", id="hdr-alerts")
        yield Static("|", classes="hdr-pipe")
        yield Static(f"refresh every {int(self._interval)}s", classes="hdr-text", id="hdr-interval")
        yield Static("--:--:--", classes="hdr-clock", id="hdr-clock")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick_clock)
        self._tick_clock()

    def _tick_clock(self) -> None:
        self.query_one("#hdr-clock", Static).update(datetime.now().strftime("%H:%M:%S"))


class DashboardFooter(Horizontal):
    """Custom bottom bar showing the key bindings."""

    DEFAULT_CSS = """
    DashboardFooter {
        height: 1;
        padding: 0 2;
        background: $header-bg;
    }
    DashboardFooter Static {
        width: auto;
        color: $header-meta;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold $header-brand]\\[q][/] quit   [bold $header-brand]\\[r][/] refresh")


class DisconnectBanner(Static):
    """Single-line banner shown when a `--connect` fetch fails.

    Hidden by default in local mode and during a healthy connect-mode
    session; toggled visible by `DashboardApp._tick` when an HTTP fetch
    raises or returns non-2xx.
    """

    DEFAULT_CSS = """
    DisconnectBanner {
        height: 0;
        padding: 0 2;
        background: $error;
        color: $text;
        text-style: bold;
    }
    DisconnectBanner.visible {
        height: 1;
    }
    """


class CheckRow(Horizontal):
    """Single row in a tile's 'CHECKS' section: name on the left, status symbol on the right."""

    DEFAULT_CSS = """
    CheckRow {
        height: 1;
    }
    CheckRow .check-name {
        width: 1fr;
        color: $text-muted;
    }
    CheckRow .check-status {
        width: auto;
        content-align: right middle;
    }
    """

    def __init__(self, check_name: str, status: Status) -> None:
        super().__init__()
        self.check_name = check_name
        self.check_status = status

    def compose(self) -> ComposeResult:
        # Strip the leading `dhis2_` namespace; keep `chap_` so chap-specific
        # checks remain distinguishable from their DHIS2 counterparts in the
        # condensed display (e.g. dhis2_ping -> ping, dhis2_chap_ping -> chap_ping).
        short = self.check_name.removeprefix("dhis2_")
        symbol = _SYMBOL_BY_STATUS.get(self.check_status, "?")
        color = _ACCENT if self.check_status is Status.OK else _color_for(self.check_status)
        yield Static(short, classes="check-name")
        yield Static(f"[{color}]{symbol}[/]", classes="check-status")


def _color_for(status: Status) -> str:
    """Return a Textual markup token for the given status (used inside `[..]` markup)."""
    return {
        Status.OK: "$success",
        Status.WARN: "$warning",
        Status.FAIL: "$error",
        Status.ERROR: "$error",
        Status.SKIPPED: "$text-disabled",
    }.get(status, "$text")


class InstanceTile(Container):
    """One tile per chap-checker target.

    Stateless: receives a `TileModel` each refresh and renders it. The
    tracking state (ping counters, history, last_report) lives in the
    daemon's `TileTracker`. Both local and connect modes hand the same
    `TileModel` shape to `apply_model`.
    """

    DEFAULT_CSS = """
    InstanceTile {
        background: $surface;
        border-left: thick $surface-lighten-2;
        padding: 1 2;
        height: 100%;
        layout: vertical;
    }
    InstanceTile.tile-status-ok {
        border-left: thick $success;
    }
    InstanceTile.tile-status-warn {
        border-left: thick $warning;
        background: $warning-muted;
    }
    InstanceTile.tile-status-fail {
        border-left: thick $error;
        background: $error-muted;
    }
    InstanceTile.tile-status-error {
        border-left: thick $error;
        background: $error-muted;
    }
    InstanceTile.tile-status-skipped {
        border-left: thick $surface-lighten-2;
    }
    InstanceTile .row {
        height: 1;
    }
    InstanceTile .tile-name {
        color: $success;
        text-style: bold;
        width: 1fr;
    }
    InstanceTile .tile-version {
        color: $text-muted;
        width: auto;
        content-align: right middle;
    }
    InstanceTile .tile-url {
        color: $text-muted;
        height: 1;
        margin-bottom: 1;
    }
    InstanceTile .pill {
        width: auto;
        padding: 0 1;
        margin-right: 2;
    }
    InstanceTile .pill-ok {
        background: $success;
        color: auto;
        text-style: bold;
    }
    InstanceTile .pill-warn {
        background: $warning;
        color: auto;
        text-style: bold;
    }
    InstanceTile .pill-fail {
        background: $error;
        color: auto;
        text-style: bold;
    }
    InstanceTile .pill-error {
        background: $error;
        color: auto;
        text-style: bold;
    }
    InstanceTile .pill-skipped {
        background: $surface-lighten-2;
        color: auto;
    }
    InstanceTile .summary {
        width: auto;
        color: $text;
        text-style: bold;
        margin-right: 3;
    }
    InstanceTile .ping {
        width: 1fr;
        color: $text-muted;
    }
    InstanceTile .checks-header {
        color: $text-disabled;
        text-style: bold;
        height: 1;
        padding-top: 1;
        padding-bottom: 0;
    }
    InstanceTile #checks {
        height: auto;
    }
    InstanceTile .tile-footer {
        dock: bottom;
        height: auto;
        layout: vertical;
    }
    InstanceTile .uptime-header {
        height: 1;
        color: $text-disabled;
        padding: 0 1;
    }
    InstanceTile UptimeStrip {
        margin-bottom: 1;
    }
    InstanceTile .stats-row {
        height: 3;
        padding-top: 1;
        align: center top;
    }
    InstanceTile .stat-cell {
        width: 1fr;
        height: 3;
    }
    InstanceTile .stat-label {
        color: $text-disabled;
        content-align: center top;
        height: 1;
    }
    InstanceTile .stat-value {
        color: $text;
        text-style: bold;
        content-align: center top;
        height: 1;
    }
    """

    def __init__(self, name: str, url: str) -> None:
        super().__init__()
        self.tile_name = name
        self.tile_url = url
        self._model: TileModel | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="row"):
            yield Static(self.tile_name.upper(), classes="tile-name")
            yield Static("", classes="tile-version", id="version")
        yield Static(f"⊕ {self.tile_url}", classes="tile-url")
        with Horizontal(classes="row"):
            yield Static("", classes="pill pill-skipped", id="pill")
            yield Static("", classes="summary", id="summary")
            yield Static("", classes="ping", id="ping")
        yield Static("CHECKS", classes="checks-header")
        yield Vertical(id="checks")
        with Vertical(classes="tile-footer"):
            yield Static(
                f"UPTIME · LAST {HISTORY_LEN} CHECKS",
                classes="uptime-header",
                id="uptime-header",
            )
            yield UptimeStrip(self._history_statuses)
            with Horizontal(classes="stats-row"):
                with Vertical(classes="stat-cell"):
                    yield Static("latency", classes="stat-label")
                    yield Static("--", classes="stat-value", id="latency")
                with Vertical(classes="stat-cell"):
                    yield Static("updated", classes="stat-label")
                    yield Static("--", classes="stat-value", id="updated")
                with Vertical(classes="stat-cell"):
                    yield Static("uptime", classes="stat-label")
                    yield Static("--", classes="stat-value", id="uptime")

    def on_mount(self) -> None:
        # Tick the "updated Xs ago" string every second.
        self.set_interval(1.0, self._tick_updated)
        # If a model was already attached before mount (the app reconciler
        # may push a snapshot before compose finishes), render it now.
        if self._model is not None:
            self._render_model()

    def _history_statuses(self) -> list[Status]:
        if self._model is None:
            return []
        return [p.status for p in self._model.history]

    def apply_model(self, model: TileModel) -> None:
        """Attach `model` and re-render. Safe to call before mount."""
        self._model = model
        if not self.is_mounted:
            return
        self._render_model()

    def _render_model(self) -> None:
        model = self._model
        assert model is not None  # guarded by callers

        self.query_one("#version", Static).update(f"DHIS2  {model.version}" if model.version else "")

        # Status pill + whole-tile status class.
        worst_status = model.worst_status
        for s in Status:
            self.set_class(s is worst_status, f"tile-status-{s.value}")
        pill = self.query_one("#pill", Static)
        pill.update(worst_status.value.upper())
        pill.set_classes(f"pill {_PILL_CLASS_BY_STATUS[worst_status]}")

        # Summary + ping.
        self.query_one("#summary", Static).update(f"{model.ok_count}/{model.total_count} checks")
        if model.ping_total > 0:
            pct = math.floor(100 * model.ping_ok / model.ping_total)
            self.query_one("#ping", Static).update(f"{model.ping_ok}/{model.ping_total} ping ({pct}%)")
        else:
            self.query_one("#ping", Static).update("")

        # Per-check rows: clear and rebuild.
        checks = self.query_one("#checks", Vertical)
        checks.remove_children()
        # CheckRowModel.name has already been stripped of `dhis2_` by the
        # daemon's projection; CheckRow strips it again as a no-op so the
        # same widget works for both naming conventions.
        for row in model.checks:
            # CheckRow's __init__ strips `dhis2_` defensively; the daemon
            # already does this, so prefix with `dhis2_` to round-trip into
            # the same display string. Skipping the prefix would render an
            # unchanged name, which still works — keep it simple.
            checks.mount(CheckRow(row.name, row.status))

        # Latency.
        if model.latency_ms is not None:
            self.query_one("#latency", Static).update(f"{model.latency_ms}ms")
        else:
            self.query_one("#latency", Static).update("--")

        # Uptime: cumulative ping ratio.
        if model.uptime_pct is not None:
            self.query_one("#uptime", Static).update(f"{model.uptime_pct:.2f}%")
        else:
            self.query_one("#uptime", Static).update("--")

        # Uptime header + strip.
        self.query_one("#uptime-header", Static).update(self._render_history_header())
        self.query_one(UptimeStrip).refresh()

        self._tick_updated()

    def _render_history_header(self) -> str:
        """Render the 'UPTIME · LAST 30 CHECKS    100%' header line."""
        label = f"UPTIME · LAST {HISTORY_LEN} CHECKS"
        if self._model and self._model.history:
            clean = sum(1 for p in self._model.history if p.status is Status.OK)
            pct = int(round(100 * clean / len(self._model.history)))
            return f"{label}  [$text-muted]{pct}%[/]"
        return label

    def _tick_updated(self) -> None:
        if self._model is None or self._model.last_refresh is None:
            return
        text = _format_relative(datetime.now(), self._model.last_refresh)
        try:
            self.query_one("#updated", Static).update(text)
        except Exception:  # noqa: BLE001
            pass


class ChapCheckerCommands(Provider):
    """Custom command-palette entries for the chap-checker TUI."""

    def _entries(self) -> list[tuple[str, str, Callable[[], Any]]]:
        app = self.app
        return [
            (
                "Refresh now",
                "Re-run every check immediately.",
                partial(app.run_action, "refresh"),
            ),
            (
                "Reload config",
                "Re-read chap-checker.toml from disk.",
                partial(app.run_action, "reload"),
            ),
            (
                "Open GitHub repository",
                "github.com/dhis2-chap/chap-checker",
                partial(app.open_url, "https://github.com/dhis2-chap/chap-checker"),
            ),
            (
                "Open documentation",
                "dhis2-chap.github.io/chap-checker",
                partial(app.open_url, "https://dhis2-chap.github.io/chap-checker/"),
            ),
        ]

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text, callback in self._entries():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)

    async def discover(self) -> Hits:
        for name, help_text, callback in self._entries():
            yield DiscoveryHit(name, callback, help=help_text)


class TokenPromptScreen(ModalScreen[str | None]):
    """Centred modal asking the operator for a bearer token.

    Shown by `DashboardApp` in `--connect` mode when the daemon requires
    auth and no token was supplied via `--token` / `--token-env`. The
    screen blocks the underlying dashboard until the operator submits a
    token (or hits Escape, which returns `None` and lets the app paint
    the auth-rejected banner). Matches the typer-prompt UX `verify --url
    --token` uses, but stays inside the Textual surface.
    """

    DEFAULT_CSS = """
    TokenPromptScreen {
        align: center middle;
    }
    TokenPromptScreen > #token-modal {
        width: auto;
        max-width: 80;
        min-width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
        layout: vertical;
    }
    TokenPromptScreen .title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    TokenPromptScreen .desc {
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
    }
    TokenPromptScreen #token-input {
        margin-bottom: 1;
    }
    TokenPromptScreen #token-row {
        height: auto;
        align: right middle;
    }
    TokenPromptScreen #token-row Button {
        margin-left: 2;
    }
    TokenPromptScreen .hint {
        color: $text-disabled;
        margin-top: 1;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, target_url: str) -> None:
        super().__init__()
        self._target_url = target_url

    def compose(self) -> ComposeResult:
        with Vertical(id="token-modal"):
            yield Label("chap-checker", classes="title")
            yield Label(
                f"This deployment requires a bearer token to view the dashboard.\nServer: {self._target_url}",
                classes="desc",
            )
            yield Input(
                id="token-input",
                placeholder="bearer token",
                password=True,
            )
            with Horizontal(id="token-row"):
                yield Button("Cancel", id="cancel-btn", variant="default")
                yield Button("Sign in", id="signin-btn", variant="primary")
            yield Label(
                "Tip: pass `--token-env NAME` next time to skip this prompt.",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#token-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Pressing Enter inside the input submits the form.
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "signin-btn":
            self._submit(self.query_one("#token-input", Input).value)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self, raw: str) -> None:
        token = (raw or "").strip()
        if not token:
            return
        self.dismiss(token)


class DashboardApp(App[None]):
    """Textual TUI for chap-checker.

    Two construction paths:

    - **Local mode** (`server` set): owns a `DashboardServer`, drives
      `refresh_once()` from `_tick`, no network.
    - **Connect mode** (`connect_url` set): polls `{URL}/api/state` via
      httpx each tick. Disconnect surfaces as a banner; the last-known
      tiles stay on screen.
    """

    CSS = """
    Screen {
        background: $background;
    }
    #grid {
        grid-gutter: 1 1;
        padding: 1;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+r", "reload", "Reload config"),
    ]

    COMMANDS = App.COMMANDS | {ChapCheckerCommands}

    def __init__(
        self,
        targets: list[TargetEntry] | None = None,
        cfg: CheckerConfig | None = None,
        config_path: Path | None = None,
        state_path: Path | None = None,
        interval_s: float = 30.0,
        alerts_enabled: bool = False,
        connect_url: str | None = None,
        connect_token: str | None = None,
    ) -> None:
        super().__init__()
        self.connect_url = connect_url
        self.connect_token = connect_token
        self.interval_s = interval_s
        self.alerts_enabled = alerts_enabled
        self.config_path = config_path
        self.tiles: dict[str, InstanceTile] = {}
        self._refreshing = False
        self._http_client: httpx.AsyncClient | None = None
        # Set once after the first auth-prompt cycle so a wrong-token 401
        # paints the auth-rejected banner instead of looping the modal.
        self._token_prompted = False
        # While the prompt screen is up we don't want the refresh interval
        # firing in the background.
        self._auth_modal_open = False

        if connect_url is None:
            if targets is None or cfg is None:
                raise ValueError("Local mode requires both `targets` and `cfg`.")
            self.server: DashboardServer | None = DashboardServer(
                targets=targets,
                cfg=cfg,
                state_path=state_path,
                interval_s=interval_s,
                alerts_enabled=alerts_enabled,
                config_path=config_path,
            )
            self._initial_state: DashboardState = self.server.snapshot()
        else:
            self.server = None
            # Until the first remote fetch lands, render with neutral
            # defaults. The header + theme are replaced from the
            # remote `DashboardState` on every successful tick.
            self._initial_state = DashboardState(
                instance_count=0,
                alerts_enabled=alerts_enabled,
                interval_s=interval_s,
                tiles=[],
                ui_title=f"chap-checker (connecting to {connect_url})",
                ui_theme="phosphor",
            )

        for builder in _THEME_BUILDERS.values():
            self.register_theme(builder())
        self.theme = _textual_theme_name(self._initial_state.ui_theme)

    def compose(self) -> ComposeResult:
        yield DashboardHeader(
            n_instances=self._initial_state.instance_count,
            alerts_enabled=self._initial_state.alerts_enabled,
            interval_s=self._initial_state.interval_s,
            title=self._initial_state.ui_title,
        )
        yield DisconnectBanner("", id="banner")
        # Mount tiles for instances already known at startup. In local
        # mode this is everything; in connect mode it's empty and the
        # first successful tick mounts the tiles dynamically.
        grid = Grid(id="grid")
        n = max(1, self._initial_state.instance_count)
        cols = columns_for(n)
        rows = math.ceil(n / cols)
        grid.styles.grid_size_columns = cols
        grid.styles.grid_size_rows = rows
        with grid:
            for tile_model in self._initial_state.tiles:
                tile = InstanceTile(tile_model.name, tile_model.url)
                tile.apply_model(tile_model)
                self.tiles[tile_model.name] = tile
                yield tile
        yield DashboardFooter()

    async def on_mount(self) -> None:
        if self.connect_url is not None:
            headers = {"Authorization": f"Bearer {self.connect_token}"} if self.connect_token else None
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                headers=headers,
            )
            # Probe `/api/auth` synchronously before the first refresh so
            # the configured `[ui].theme` is applied before any modal
            # paints. Without this the TokenPromptScreen would render in
            # the default Textual theme (phosphor green) and snap to the
            # real theme only after sign-in. Same idea as the browser
            # SPA's eager /api/auth probe.
            await self._probe_remote_theme()
        self.set_interval(self.interval_s, self.action_refresh)
        self.call_after_refresh(self.action_refresh)

    async def _probe_remote_theme(self) -> None:
        """Best-effort fetch of `/api/auth` to learn the daemon's `[ui].theme`.

        Failures are swallowed - if the probe can't reach the daemon the
        TUI just stays on phosphor until `/api/state` returns. The
        endpoint is unauthenticated so this works whether or not
        `[auth]` is configured.
        """
        if self.connect_url is None:
            return
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as probe:
                response = await probe.get(self.connect_url.rstrip("/") + "/api/auth")
            if response.status_code != 200:
                return
            data = response.json()
        except Exception:  # noqa: BLE001 - any failure falls through silently
            return
        remote_theme = data.get("ui_theme") if isinstance(data, dict) else None
        if isinstance(remote_theme, str):
            try:
                self.theme = _textual_theme_name(remote_theme)
            except Exception:  # noqa: BLE001 - unknown theme value just stays on phosphor
                pass

    async def on_unmount(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()

    async def _fetch_state(self) -> DashboardState | None:
        """Produce a fresh snapshot, or `None` if the remote fetch failed.

        Local mode runs the embedded server's refresh and returns its
        snapshot. Connect mode GETs `/api/state`. Any transport error or
        non-2xx is converted to `None` so the caller can paint the
        disconnect banner without crashing the loop.
        """
        if self.server is not None:
            await self.server.refresh_once()
            return self.server.snapshot()
        assert self.connect_url is not None and self._http_client is not None
        url = self.connect_url.rstrip("/") + "/api/state"
        try:
            response = await self._http_client.get(url)
            # Distinct handling for auth failure. First 401 with no
            # stored token: pop the in-TUI modal and let the operator
            # paste a token. Subsequent 401s (after the modal closed):
            # paint the auth-rejected banner so the operator knows the
            # token is wrong / expired. Don't raise_for_status before
            # checking the status code ourselves.
            if response.status_code == 401:
                if not self._token_prompted and self.connect_token is None:
                    await self._prompt_for_token()
                    return None
                hint = "set --token-env" if self.connect_token is None else "check the token value"
                self._set_disconnect_banner(f"auth rejected by {self.connect_url}: {hint}")
                return None
            response.raise_for_status()
            return DashboardState.model_validate_json(response.content)
        except Exception as exc:  # noqa: BLE001 - banner-paint covers any transport / parse error
            self._set_disconnect_banner(f"disconnected from {self.connect_url}: {exc}")
            return None

    async def _prompt_for_token(self) -> None:
        """Pop the TokenPromptScreen non-blockingly; result is handled by callback.

        Uses `push_screen(..., callback=...)` rather than
        `push_screen_wait(...)` because the wait variant needs a worker
        context and we're already inside an action handler. The callback
        rebuilds the httpx client with the new token and triggers an
        immediate refresh - same end-to-end semantics, lower ceremony.
        """
        if self._auth_modal_open or self.connect_url is None:
            return
        self._auth_modal_open = True
        self.push_screen(TokenPromptScreen(self.connect_url), self._on_token_submitted)

    def _on_token_submitted(self, token: str | None) -> None:
        """Callback fired when TokenPromptScreen dismisses.

        Receives the operator-typed token (or `None` if they cancelled).
        Builds a fresh `httpx.AsyncClient` with the bearer header (httpx
        clients carry immutable headers per client, so updating means a
        new client) and kicks off an immediate refresh.
        """
        self._auth_modal_open = False
        self._token_prompted = True
        if not token:
            self._set_disconnect_banner(
                f"auth rejected by {self.connect_url}: set --token-env or sign in",
            )
            return
        self.connect_token = token
        # Schedule an async worker to close the old client + open a new
        # one with the bearer header, then refresh. The callback is sync
        # but `aclose()` needs an event loop.
        self.run_worker(self._reopen_client_and_refresh(), exclusive=True)

    async def _reopen_client_and_refresh(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Authorization": f"Bearer {self.connect_token}"},
        )
        await self.action_refresh()

    def _set_disconnect_banner(self, message: str | None) -> None:
        try:
            banner = self.query_one("#banner", DisconnectBanner)
        except Exception:  # noqa: BLE001 - banner not mounted yet
            return
        if message is None:
            banner.update("")
            banner.remove_class("visible")
        else:
            banner.update(message)
            banner.add_class("visible")

    async def action_refresh(self) -> None:
        """Drive one refresh: fetch a snapshot, reconcile tiles, update header."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            state = await self._fetch_state()
            if state is None:
                return
            self._set_disconnect_banner(None)
            await self._reconcile_tiles(state)
            self._update_header(state)
        finally:
            self._refreshing = False

    async def _reconcile_tiles(self, state: DashboardState) -> None:
        """Add/remove/update tile widgets to match `state.tiles`."""
        grid = self.query_one("#grid", Grid)
        incoming = {t.name: t for t in state.tiles}
        for gone in list(self.tiles.keys() - incoming.keys()):
            tile = self.tiles.pop(gone)
            await tile.remove()
        for name, tile_model in incoming.items():
            if name not in self.tiles:
                new_tile = InstanceTile(name, tile_model.url)
                self.tiles[name] = new_tile
                await grid.mount(new_tile)
            self.tiles[name].apply_model(tile_model)
        # Resize the grid in case the instance count changed.
        n = max(1, state.instance_count)
        cols = columns_for(n)
        rows = math.ceil(n / cols)
        grid.styles.grid_size_columns = cols
        grid.styles.grid_size_rows = rows

    def _update_header(self, state: DashboardState) -> None:
        """Refresh the header fields from the latest snapshot."""
        try:
            self.query_one("#hdr-name", Static).update(state.ui_title)
            self.query_one("#hdr-instances", Static).update(f"{state.instance_count} instance(s)")
            self.query_one("#hdr-alerts", Static).update(f"alerts {'ON' if state.alerts_enabled else 'OFF'}")
            self.query_one("#hdr-interval", Static).update(f"refresh every {int(state.interval_s)}s")
        except Exception:  # noqa: BLE001 - header may not be mounted yet
            return
        # Swap theme if the server's `[ui].theme` changed (only meaningful
        # in connect mode, where the server picks the theme).
        try:
            new_theme = _textual_theme_name(state.ui_theme)
            if self.theme != new_theme:
                self.theme = new_theme
        except Exception:  # noqa: BLE001 - bad theme name shouldn't crash the loop
            pass

    async def action_reload(self) -> None:
        """Re-read `chap-checker.toml` and apply the new config in place.

        Only meaningful in local mode. In connect mode the daemon owns
        the config; hit `POST /api/reload` on the remote instead.
        """
        if self.connect_url is not None:
            self.notify("In --connect mode; the remote daemon owns the config.", severity="warning")
            return
        if self.config_path is None or self.server is None:
            self.notify("No config path - running with ad-hoc target.", severity="warning")
            return
        try:
            new_cfg = load_config(self.config_path)
        except Exception as exc:  # noqa: BLE001 - any error surfaces as a toast
            self.notify(f"Reload failed: {exc}", severity="error")
            return
        # Apply the new config to the embedded server. The next tick will
        # pick up the new tiles via reconciliation.
        new_targets = [
            entry.to_target_entry(name, default_retry_policy=new_cfg.retry) for name, entry in new_cfg.instances.items()
        ]
        old_names = {t.name for t in self.server.targets}
        new_names = {t.name for t in new_targets}
        old_theme = self.server.cfg.ui.theme
        self.server.targets = new_targets
        self.server.cfg = new_cfg
        for gone in old_names - new_names:
            self.server.trackers.pop(gone, None)
        if new_cfg.ui.theme != old_theme:
            try:
                self.theme = _textual_theme_name(new_cfg.ui.theme)
            except Exception as exc:  # noqa: BLE001 - bad theme name shouldn't crash
                self.notify(f"Theme swap failed: {exc}", severity="warning")
        if old_names != new_names:
            added = sorted(new_names - old_names)
            removed = sorted(old_names - new_names)
            parts: list[str] = []
            if added:
                parts.append(f"+{len(added)} ({', '.join(added)})")
            if removed:
                parts.append(f"-{len(removed)} ({', '.join(removed)})")
            self.notify(f"Instance set changed: {' '.join(parts)}.")
        else:
            self.notify(f"Reloaded {self.config_path.name} ({len(self.server.targets)} instance(s)).")


def run(
    targets: list[TargetEntry] | None = None,
    cfg: CheckerConfig | None = None,
    config_path: Path | None = None,
    state_path: Path | None = None,
    interval_s: float = 30.0,
    alerts_enabled: bool = False,
    connect_url: str | None = None,
    connect_token: str | None = None,
) -> None:
    """Launch the TUI dashboard (local or `--connect` mode)."""
    DashboardApp(
        targets=targets,
        cfg=cfg,
        config_path=config_path,
        state_path=state_path,
        interval_s=interval_s,
        alerts_enabled=alerts_enabled,
        connect_url=connect_url,
        connect_token=connect_token,
    ).run()


__all__ = [
    "CheckRow",
    "DashboardApp",
    "DashboardFooter",
    "DashboardHeader",
    "DisconnectBanner",
    "InstanceTile",
    "UptimeStrip",
    "Widget",
    "columns_for",
    "run",
]
