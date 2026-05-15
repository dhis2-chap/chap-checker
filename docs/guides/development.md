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
├── cli.py                 # typer entry point (verify / checks / alerts / dashboard)
├── client.py              # httpx-based Dhis2Client + Dhis2Target
├── config.py              # TOML loader; CheckerConfig, InstanceConfig, SlackAlertConfig
├── runner.py              # parallel run_targets, RunReport, VerifyReport, TargetEntry
├── output.py              # Rich table + JSON renderers
├── state.py               # GlobalState (CLI flags container)
├── state_store.py         # state file load/save + compute_transitions
├── logging.py             # stderr-only logger config
├── dashboard.py           # Textual TUI
├── alerts/
│   ├── base.py            # Alerter protocol + Transition + register_alerter
│   └── slack.py           # SlackAlerter
└── checks/
    ├── base.py            # Check protocol + register_check + resolve_checks
    ├── dhis2_ping.py
    ├── dhis2_system_info.py
    ├── dhis2_chap_route.py
    ├── dhis2_chap_ping.py
    ├── dhis2_chap_system_info.py
    ├── dhis2_chap_modeling_app.py
    └── dhis2_chap_climate_app.py
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
The dashboard module's update logic is decoupled from rendering (data goes
into `update_from`, UI updates are guarded by `self.is_mounted`) so unit
tests can poke the data path without an active app.
