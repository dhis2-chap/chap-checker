# Development

## Setup

```bash
git clone https://github.com/dhis2-chap/chap-checker.git
cd chap-checker
make install
```

chap-checker is a Rust binary (edition 2024, stable toolchain). `make install`
runs `uv sync` for the dev tools (mkdocs for the docs site, Playwright for the
browser captures, maturin for the wheel) and `cargo build` for the binary.

## Make targets

| Target                 | What it does                                                         |
| ---------------------- | -------------------------------------------------------------------- |
| `make install`         | `uv sync` (dev tools) and `cargo build`                              |
| `make lint`            | `cargo fmt`, then clippy with warnings as errors                     |
| `make check`           | The same without writing: what CI runs                               |
| `make test`            | `cargo test`: unit tests, and integration tests against mock servers |
| `make build`           | `uv build`: the wheel and sdist into `dist/`, as the release does   |
| `make docs`            | `mkdocs serve` on <http://127.0.0.1:8000>                            |
| `make docs-build`      | Regenerate the CLI reference, then `mkdocs build` into `site/`       |
| `make docs-cli`        | Regenerate `docs/cli-reference.md` from the CLI                     |
| `make docs-screenshot` | Re-capture the TUI screenshots in `docs/assets/` (both themes)       |
| `make clean`           | `cargo clean`, and remove `dist/` and `site/`                        |

## Repo layout

```
crates/chap-checker/
├── Cargo.toml             # the crate; sokkel-tui pinned by commit
├── web_ui/                # the browser dashboard served by `serve` (React, no build step), built into the binary
└── src/
    ├── main.rs            # the CLI (clap): init, verify, checks, plugins, alerts, serve, tui
    ├── config.rs          # chap-checker.toml: parsing and validation
    ├── init_template.toml # what `chap-checker init` writes
    ├── http.rs            # targets, the HTTP client, retries
    ├── plugins.rs         # the kinds: dhis2, ogs, chap, caps, http
    ├── checks/            # the registry (mod.rs) and each plugin's checks
    ├── runner.rs          # runs checks, several targets at a time
    ├── state_store.rs     # the state file and per-alerter transitions
    ├── alerts.rs          # Slack and webhook alerters, dispatch
    ├── output.rs          # verify's tables; jsonfmt.rs its JSON
    ├── daemon.rs          # the refresh loop and the /api/state snapshot
    ├── serve.rs           # the HTTP daemon (axum)
    └── tui.rs             # the terminal dashboard (sokkel-tui)
scripts/                   # capture scripts for the docs and for visual checks
docs/                      # this site (mkdocs)
pyproject.toml             # maturin: builds the PyPI wheel from the crate
```

## House rules

- **Never panic on a server's answer.** A check turns anything unexpected
  into a `FAIL` or `ERROR` with a message an operator can act on.
- **Never echo a secret.** Config errors name the field and the problem, not
  the value; check messages and `details` carry no credentials.
- **Keep `verify --json` stable.** Scripts parse it: same keys, same shape.
  `jsonfmt` sorts keys explicitly; don't rely on a map's order.
- **The TUI uses sokkel-tui's parts.** When something is missing (a part, a
  behaviour), it goes into sokkel-tui, not into a workaround here.
- **Visual changes are checked visually.** See below.

## Tests

```bash
cargo test                          # everything
cargo test --test checks            # the checks against a mock server
cargo test state_store              # by name
```

Integration tests run checks, alert dispatch and the HTTP daemon against
`wiremock` servers; TUI tests draw whole frames with sokkel-tui's
`app::shot` and read them as text.

## Visual checks

- **TUI**: `scripts/capture_tui_rs.sh OUT [COLSxROWS] [tui args...]` draws one
  frame to SVG, text and PNG; `make docs-screenshot` re-captures the docs
  images in both themes.
- **Browser**: `uv run python scripts/capture_browser.py --url URL --token TOKEN`
  drives the login modal, the dashboard and sign-out (plus `--palette` and
  `--states`) into PNGs.

Look at the images, not just at the files existing.
