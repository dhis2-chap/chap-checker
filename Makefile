.PHONY: help install lint check test build docs docs-build docs-cli docs-screenshot clean

# chap-checker is a Rust binary (crates/chap-checker); uv only carries the docs site, the
# browser capture script and maturin, which builds the PyPI wheel.

UV := $(shell command -v uv 2> /dev/null)
BIN := target/debug/chap-checker

# ==============================================================================
# Targets
# ==============================================================================

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install         Install the dev tools (mkdocs, playwright, maturin) and build the binary"
	@echo "  lint            Format, and lint with clippy"
	@echo "  check           Check formatting and lint without writing changes"
	@echo "  test            Run the tests"
	@echo "  build           Build the wheel and sdist into dist/ (what the release uploads)"
	@echo "  docs            Serve docs locally at http://127.0.0.1:8000"
	@echo "  docs-build      Build the static docs site into site/"
	@echo "  docs-cli        Regenerate docs/cli-reference.md from the CLI"
	@echo "  docs-screenshot Re-capture the TUI screenshots in docs/assets/ (needs ./chap-checker.toml)"
	@echo "  clean           Clean up build output"

install:
	@echo ">>> Installing dev tools"
	@$(UV) sync
	@echo ">>> Building chap-checker"
	@cargo build

lint:
	@echo ">>> Formatting"
	@cargo fmt --all
	@echo ">>> Running clippy"
	@cargo clippy --all-targets --locked -- -D warnings

check:
	@echo ">>> Checking formatting"
	@cargo fmt --all --check
	@echo ">>> Running clippy"
	@cargo clippy --all-targets --locked -- -D warnings

test:
	@echo ">>> Running tests"
	@cargo test --locked

build:
	@echo ">>> Building the wheel and sdist"
	@$(UV) build

docs:
	@echo ">>> Serving docs at http://127.0.0.1:8000"
	@$(UV) run mkdocs serve

docs-build: docs-cli
	@echo ">>> Building docs into site/"
	@$(UV) run mkdocs build

docs-cli:
	@echo ">>> Regenerating docs/cli-reference.md from the CLI"
	@cargo build --quiet
	@$(BIN) markdown-help > docs/cli-reference.md
	@echo "    wrote docs/cli-reference.md"

docs-screenshot:
	@echo ">>> Capturing the TUI screenshots into docs/assets/ (both themes)"
	@scripts/capture_docs_tui.sh chap-checker.toml

clean:
	@echo ">>> Cleaning up"
	@cargo clean
	@rm -rf dist site

# ==============================================================================
# Default
# ==============================================================================

.DEFAULT_GOAL := help
