# Contributing to chap-checker

Thanks for taking the time to look at chap-checker. This page covers the rules and the rough mechanics — most of the operational guidance for working in the code lives on the docs site at <https://dhis2-chap.github.io/chap-checker/guides/development/>.

## Getting the project running

chap-checker is a Rust binary (`crates/chap-checker`, stable toolchain). [`uv`](https://docs.astral.sh/uv/) carries the dev tools around it (the docs site, the browser capture script, and maturin, which builds the PyPI wheel); `pip` is not supported.

```bash
git clone https://github.com/dhis2-chap/chap-checker.git
cd chap-checker
make install              # uv sync (dev tools) + cargo build
make check                # cargo fmt --check + clippy -D warnings
make test                 # cargo test
make docs                 # mkdocs serve on http://127.0.0.1:8000
```

A typical change cycle is:

```bash
make lint                 # cargo fmt + clippy
make test                 # run tests
make docs-cli             # regenerate docs/cli-reference.md from the CLI
                          # (only when you've touched the CLI surface)
make build                # the wheel + sdist, as the release builds them
```

## Project rules

These come up in code review every time, so they're worth stating once:

1. **No emojis.** Anywhere — code, comments, docs, commit messages, PR descriptions, chat output.
2. **No Claude Code attribution.** Don't add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or similar.
3. **Conventional Commits** for commit messages, branch names, and PR titles.
   - `<type>(<scope>)?: <description>` (e.g. `feat(ci): add docker publish workflow`, `fix(main): correct db path creation`).
   - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
   - Branch names: `<type>/<short-description>` (e.g. `feat/makefile-and-ci`, `fix/sqlite-path`).
4. **`uv` only.** `uvx chap-checker ...` for one-shots, `uv tool install / upgrade chap-checker` for persistent installs. Don't write `pip install` in code, docs, or chat output.
5. **Clippy clean.** `make check` runs clippy with warnings as errors; CI is the gate.
6. **Never panic on a server's answer, never echo a secret.** A check turns anything unexpected into a `FAIL` or `ERROR` with a message an operator can act on; config errors and check results never carry a credential.
7. **The TUI uses sokkel-tui's parts.** sokkel-tui is pinned by commit in `crates/chap-checker/Cargo.toml`; move the pin on purpose. A missing part or behaviour is reported to sokkel-tui, not worked around here.

## Where things live

chap-checker is an uptime monitor with plugins. The core is the runner, the state file and alerts, `verify`, `serve` and the TUI. Each kind of server (`kind = "..."`) is a plugin, compiled in: `dhis2`, `ogs`, `chap` (chap-core reached directly), `caps` (CAPS and its links to chap-core and DHIS2) and `http` (any URL, with expectations written in the config). `chap-checker plugins list` shows each one.

```
crates/chap-checker/
├── web_ui/                # the browser dashboard `serve` serves, built into the binary
└── src/
    ├── main.rs            # the CLI (clap)
    ├── config.rs          # chap-checker.toml: parsing and validation
    ├── init_template.toml # what `chap-checker init` writes (mirror chap-checker.toml.example)
    ├── http.rs            # targets, the HTTP client, retries
    ├── plugins.rs         # the kinds: dhis2, ogs, chap, caps, http
    ├── checks/            # the registry (mod.rs) and each plugin's checks: dhis2.rs, ogs.rs,
    │                      # chap.rs, caps.rs, http_expect.rs; http_2xx.rs is shared
    ├── runner.rs          # run_targets, RunReport, VerifyReport
    ├── state_store.rs     # state file and per-alerter transitions
    ├── alerts.rs          # Slack and webhook alerters, dispatch
    ├── output.rs          # verify's tables; jsonfmt.rs its JSON
    ├── daemon.rs          # refresh loop, per-tile trackers, the /api/state snapshot
    ├── serve.rs           # axum app: /api/*, auth, the browser dashboard
    └── tui.rs             # the terminal dashboard on sokkel-tui (local and --connect)
pyproject.toml             # maturin: builds the wheel from the crate (`bindings = "bin"`)
scripts/                   # capture scripts: capture_tui_rs.sh, capture_docs_tui.sh, capture_browser.py
```

## Adding things

- **A new check** — see `docs/guides/custom-checks.md`. Short version: a `CheckDef` in `checks/<plugin>.rs` with its `kinds`, `order` and `requires`, registered in `checks/mod.rs`, with a mock-server test in `tests/checks.rs`.
- **A new plugin** (a kind of server) — same guide. Short version: a `TargetKind` variant and its entry in `plugins.rs`, its checks, its config fields in `config.rs`, tests, and an example in `init_template.toml` and `chap-checker.toml.example`.
- **A new alerter** — see `docs/guides/alerts.md#custom-payload-shape`. Short version: a `Flavor` and payload function in `alerts.rs`, its `[alerts.<name>]` table in `config.rs`, and an entry in `ALERTER_TYPES` with a TOML example so `chap-checker alerts list` shows it.

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
