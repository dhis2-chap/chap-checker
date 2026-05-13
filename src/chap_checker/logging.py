"""Logging configuration for the CLI."""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "chap_checker"


def configure(debug: bool) -> None:
    """Configure the root chap_checker logger.

    Args:
        debug: If True, set the logger to DEBUG, otherwise WARNING. All logs
            are written to stderr so that ``--json`` output on stdout stays
            machine-readable.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    logger.propagate = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger scoped under ``chap_checker``."""
    if name is None:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
