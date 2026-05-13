.PHONY: help install lint check test coverage clean docs docs-build docs-cli docs-screenshot

# ==============================================================================
# Venv
# ==============================================================================

UV := $(shell command -v uv 2> /dev/null)
VENV_DIR?=.venv
PYTHON := $(VENV_DIR)/bin/python

# ==============================================================================
# Targets
# ==============================================================================

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install         Install dependencies"
	@echo "  lint            Format, lint and type-check (auto-fixes)"
	@echo "  check           Lint and type-check without writing changes"
	@echo "  test            Run tests"
	@echo "  coverage        Run tests with coverage reporting"
	@echo "  docs            Serve docs locally at http://127.0.0.1:8000"
	@echo "  docs-build      Build the static docs site into site/"
	@echo "  docs-cli        Regenerate docs/cli-reference.md from the Typer app"
	@echo "  docs-screenshot Re-capture docs/assets/dashboard.svg (needs ./chap-checker.toml)"
	@echo "  clean           Clean up temporary files"

install:
	@echo ">>> Installing dependencies"
	@$(UV) sync --all-extras

lint:
	@echo ">>> Running formatter"
	@$(UV) run ruff format .
	@echo ">>> Running linter"
	@$(UV) run ruff check . --fix
	@echo ">>> Running type checker"
	@$(UV) run mypy --explicit-package-bases src tests
	@$(UV) run pyright

check:
	@echo ">>> Checking formatting"
	@$(UV) run ruff format --check .
	@echo ">>> Running linter"
	@$(UV) run ruff check .
	@echo ">>> Running type checker"
	@$(UV) run mypy --explicit-package-bases src tests
	@$(UV) run pyright

test:
	@echo ">>> Running tests"
	@$(UV) run pytest -q

coverage:
	@echo ">>> Running tests with coverage"
	@$(UV) run coverage run -m pytest -q
	@$(UV) run coverage report
	@$(UV) run coverage xml

docs:
	@echo ">>> Serving docs at http://127.0.0.1:8000"
	@$(UV) run mkdocs serve

docs-build: docs-cli
	@echo ">>> Building docs into site/"
	@$(UV) run mkdocs build

docs-cli:
	@echo ">>> Regenerating docs/cli-reference.md from the Typer app"
	@$(UV) run typer chap_checker.cli utils docs --name chap-checker --title "CLI reference" --output docs/cli-reference.md
	@echo "    wrote docs/cli-reference.md"

docs-screenshot:
	@echo ">>> Capturing dashboard screenshot to docs/assets/dashboard.svg"
	@$(UV) run python scripts/capture_dashboard.py

clean:
	@echo ">>> Cleaning up"
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .coverage htmlcov coverage.xml
	@rm -rf .pyright
	@rm -rf dist build *.egg-info
	@rm -rf site

# ==============================================================================
# Default
# ==============================================================================

.DEFAULT_GOAL := help
