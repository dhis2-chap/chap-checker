import pytest

from chap_checker.checks import all_checks, resolve_checks


def test_none_returns_every_registered_check() -> None:
    assert {c.name for c in resolve_checks(None)} == {c.name for c in all_checks()}


def test_named_subset_returns_only_those() -> None:
    selected = resolve_checks(["dhis2_ping"])
    assert [c.name for c in selected] == ["dhis2_ping"]


def test_transitive_requires_are_pulled_in() -> None:
    """Asking for the chap system-info check pulls in ping/route/chap_ping."""
    selected = resolve_checks(["dhis2_chap_system_info"])
    names = [c.name for c in selected]
    # Canonical (order, name) order: 10, 30, 40, 50.
    assert names == ["dhis2_ping", "dhis2_chap_route", "dhis2_chap_ping", "dhis2_chap_system_info"]


def test_unknown_name_raises() -> None:
    with pytest.raises(KeyError, match="unknown check"):
        resolve_checks(["does-not-exist"])


def test_empty_list_returns_empty() -> None:
    assert resolve_checks([]) == []
