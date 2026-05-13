"""chap-checker - run a suite of checks against a DHIS2 server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("chap-checker")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
