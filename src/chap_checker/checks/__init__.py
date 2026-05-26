"""Check implementations.

Importing this package triggers each check module, which registers itself
via :func:`chap_checker.checks.base.register_check`.
"""

from chap_checker.checks import (
    dhis2_chap_climate_app,
    dhis2_chap_modeling_app,
    dhis2_chap_ping,
    dhis2_chap_route,
    dhis2_chap_system_info,
    dhis2_ping,
    dhis2_system_info,
    http_2xx,
)
from chap_checker.checks.base import (
    Check,
    CheckResult,
    Status,
    all_checks,
    register_check,
    resolve_checks,
)

__all__ = [
    "Check",
    "CheckResult",
    "Status",
    "all_checks",
    "dhis2_chap_climate_app",
    "dhis2_chap_modeling_app",
    "dhis2_chap_ping",
    "dhis2_chap_route",
    "dhis2_chap_system_info",
    "dhis2_ping",
    "dhis2_system_info",
    "http_2xx",
    "register_check",
    "resolve_checks",
]
