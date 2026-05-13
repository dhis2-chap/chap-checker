.PHONY: help install lint check test coverage clean

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
	@echo "  install      Install dependencies"
	@echo "  lint         Format, lint and type-check (auto-fixes)"
	@echo "  check        Lint and type-check without writing changes"
	@echo "  test         Run tests"
	@echo "  coverage     Run tests with coverage reporting"
	@echo "  clean        Clean up temporary files"

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

# ==============================================================================
# Default
# ==============================================================================

.DEFAULT_GOAL := help
