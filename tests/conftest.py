"""Shared pytest fixtures.

The CLI tests assert substrings of typer's Rich-rendered ``--help`` output.
On CI (no TTY) Rich falls back to its detected terminal width, which is
narrow enough that flag names get column-clipped (``--concurrency`` becomes
``--concurren ...``) and the substring assertions fail. Pinning ``COLUMNS``
to a wide value for the test session keeps Rich rendering everything on one
line, which is what the assertions expect.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _wide_terminal_for_help_tests() -> Iterator[None]:
    """Pin ``COLUMNS`` so Rich-rendered help output doesn't clip flag names."""
    previous = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "200"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = previous
