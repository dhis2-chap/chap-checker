# Development

## Setup

```bash
git clone https://github.com/dhis2-chap/chap-checker
cd chap-checker
make install
```

`make install` is a thin wrapper around `uv sync --all-extras`. Python 3.13+
is required.

## Make targets

| Target            | What it does |
| ----------------- | ------------ |
| `make install`    | `uv sync --all-extras` |
| `make lint`       | ruff format, ruff lint (with `--fix`), mypy strict, pyright strict |
| `make check`      | same as `lint` but no auto-fixes; used in CI |
| `make test`       | `uv run pytest -q` |
| `make coverage`   | pytest with coverage report + xml |
| `make docs`       | serve the docs locally at <http://127.0.0.1:8000> |
| `make docs-build` | build the docs into `site/` |
| `make clean`      | nuke caches, build artefacts, coverage output |

## Repo layout

```
src/chap_checker/
├── cli.py                 # typer entry point (init / verify / tui / serve / alerts / checks)
├── client.py              # httpx-based Dhis2Client + Dhis2Target
├── config.py              # TOML loader; CheckerConfig, InstanceConfig, SlackAlertConfig,
│                          # WebhookAlertConfig, AuthConfig, UiConfig, RetryConfig
├── runner.py              # parallel run_targets, RunReport, VerifyReport, TargetEntry
├── output.py              # Rich table + JSON renderers
├── state.py               # GlobalState (CLI flags container)
├── state_store.py         # state file load/save + compute_transitions
│                          # + alert_state_lock (fcntl.flock async ctx mgr, POSIX-only)
├── logging.py             # stderr-only logger config
├── daemon.py              # DashboardServer (refresh loop + per-tile trackers + DashboardState)
├── dashboard.py           # Textual TUI; embeds DashboardServer in local mode, httpx-polls in --connect mode
├── serve.py               # FastAPI app exposing the daemon's /api/state + browser bundle (chap-checker serve)
├── alerts/
│   ├── __init__.py        # re-exports + builtins import (registers Slack + webhook)
│   ├── base.py            # Alerter protocol + Transition + register_alerter
│   ├── slack.py           # SlackAlerter (subclass of WebhookAlerter)
│   └── webhook.py         # Generic WebhookAlerter (canonical JSON envelope)
├── checks/
│   ├── __init__.py        # explicit imports so @register_check fires on package load
│   ├── base.py            # Check protocol + register_check + resolve_checks + diagnose_status
│   ├── dhis2_ping.py
│   ├── dhis2_system_info.py
│   ├── dhis2_chap_route.py
│   ├── dhis2_chap_ping.py
│   ├── dhis2_chap_system_info.py
│   ├── dhis2_chap_modeling_app.py
│   └── dhis2_chap_climate_app.py
└── web_ui/                # FastAPI-served browser dashboard (React/Babel-standalone, no build)
    ├── index.html
    ├── vendor/            # React 18 UMD + Babel 7 standalone (committed)
    ├── _state.js          # wiring layer (polls /api/state, maps to artifact shape, auth)
    └── src/               # designer artifact (replaced wholesale on next zip drop)
        ├── app.jsx
        ├── card.jsx
        ├── palette.jsx
        └── tweaks-panel.jsx
```

## House rules

- **pydantic for every data class**. No `@dataclass`, no `attrs`, no
  `NamedTuple` / `TypedDict` for things that hold data.
- **httpx for every HTTP call**. No `requests`, no `urllib`.
- **No emojis** in code, comments, commit messages, PR descriptions.
- **No Claude / AI attribution** on commits or PRs.
- **Conventional Commits** — `feat(scope): ...`, `fix(scope): ...`,
  `docs(scope): ...`, etc. Branch names follow `<type>/<short-description>`.

## Strict typing

Both `mypy --strict` and `pyright --strict` run on every `make lint`. The
project uses `ClassVar[...]` annotations on Protocol implementations
(`Check`, `Alerter`) so the decorator-style class registration type-checks
cleanly.

## Testing

```bash
make test                            # all tests
uv run pytest tests/test_dashboard.py -v
uv run pytest -k state_store
```

Test fakes for the Check protocol use `cast(Check, ...)` to satisfy the
strict Protocol type without duplicating the `ClassVar` declarations on
test-only classes.

Textual UI tests rely on `pilot` mode rather than launching a real terminal.
The dashboard's data model lives in `daemon.py` (`DashboardServer`,
`TileTracker`, `TileModel`), so unit tests can exercise the
ping-counter / history / projection logic without an active app, and
`pilot` mode covers the rendering path end-to-end.
