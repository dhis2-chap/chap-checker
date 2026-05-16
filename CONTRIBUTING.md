# Contributing to chap-checker

Thanks for taking the time to look at chap-checker. This page covers the rules and the rough mechanics — most of the operational guidance for working in the code lives on the docs site at <https://dhis2-chap.github.io/chap-checker/guides/development/>.

## Getting the project running

The repo uses [`uv`](https://docs.astral.sh/uv/) for everything; `pip` is not supported.

```bash
git clone https://github.com/dhis2-chap/chap-checker.git
cd chap-checker
make install              # uv sync --all-extras
make check                # ruff format-check + ruff lint + mypy + pyright
make test                 # pytest
make docs                 # mkdocs serve on http://127.0.0.1:8000
```

A typical change cycle is:

```bash
make lint                 # auto-format + auto-fix lint + type-check
make test                 # run tests
make docs-cli             # regenerate docs/cli-reference.md from the Typer app
                          # (only when you've touched the CLI surface)
```

## Project rules

These come up in code review every time, so they're worth stating once:

1. **No emojis.** Anywhere — code, comments, docs, commit messages, PR descriptions, chat output.
2. **No Claude Code attribution.** Don't add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or similar.
3. **Conventional Commits** for commit messages, branch names, and PR titles.
   - `<type>(<scope>)?: <description>` (e.g. `feat(ci): add docker publish workflow`, `fix(main): correct db path creation`).
   - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
   - Branch names: `<type>/<short-description>` (e.g. `feat/makefile-and-ci`, `fix/sqlite-path`).
4. **`uv` only.** `uvx chap-checker ...` for one-shots, `uv tool install / upgrade chap-checker` for persistent installs, `uv add chap-checker` for embedding in another project. Don't write `pip install` in code, docs, or chat output.
5. **`pydantic` for every data class.** No `@dataclass`, no `attrs`, no `NamedTuple` / `TypedDict` for things that hold data.
6. **`httpx` for every HTTP call.** No `requests`, no `urllib`, no `aiohttp`.
7. **Strict typing.** `mypy --strict` and `pyright --strict` both run on every `make check`. New code should pass both. Use `ClassVar[...]` on Protocol implementations (`Check`, `Alerter`) so the decorator-style registration type-checks cleanly.

## Where things live

```
src/chap_checker/
├── cli.py                 # typer entry point (verify / checks / alerts / tui / serve / init)
├── client.py              # Dhis2Target wrapper around dhis2w_client.Dhis2Client
├── config.py              # TOML loader; CheckerConfig, InstanceConfig, *AlertConfig
├── runner.py              # parallel run_targets, RunReport, VerifyReport, TargetEntry
├── output.py              # Rich table + JSON renderers
├── state_store.py         # state file load/save + compute_transitions
├── daemon.py              # DashboardServer (refresh loop + per-tile trackers + DashboardState)
├── dashboard.py           # Textual TUI; embeds DashboardServer locally, httpx-polls in --connect mode
├── serve.py               # FastAPI app exposing the daemon's /api/state + browser bundle (chap-checker serve)
├── alerts/                # registered alerters (slack, webhook). Add a new one: drop a module + decorate + import.
└── checks/                # registered checks. Add a new one: drop a module + decorate + import.
```

## Adding things

- **A new check** — see `docs/guides/custom-checks.md`. Short version: subclass `Check` (Protocol), decorate with `@register_check`, add the module import to `src/chap_checker/checks/__init__.py`.
- **A new alerter** — see `docs/guides/alerts.md`. Short version: subclass `WebhookAlerter` if it's an HTTP receiver (just override `_build_payload`), or implement `Alerter` directly otherwise. Decorate with `@register_alerter("name")`, add a `[alerts.<name>]` config model in `config.py`, and add the module import to `src/chap_checker/alerts/__init__.py`. Ship a `toml_example` ClassVar so `chap-checker alerts list` can show your alerter to operators.

## Pull requests

- Open against `main`.
- One logical change per PR. A docs-only or test-only PR is fine; mixing unrelated features is not.
- The PR template has a Summary, Test plan, and Breaking changes section — fill all three.
- `make check && make test` must pass locally before pushing; CI runs them anyway as the gate.
- Updates the CHANGELOG.md "Unreleased" section if the change is user-visible.

## Releasing

Maintainer-only. See `docs/guides/releasing.md` for the bump → tag → push playbook.

## Contributors

The git history is the source of truth. The full list is on [the GitHub contributors graph](https://github.com/dhis2-chap/chap-checker/graphs/contributors).
