import pytest

from chap_checker.checks import all_checks, resolve_checks


def test_none_returns_every_registered_check() -> None:
    assert {c.name for c in resolve_checks(None)} == {c.name for c in all_checks()}


def test_named_subset_returns_only_those() -> None:
    selected = resolve_checks(["dhis2_chap_ping"])
    assert [c.name for c in selected] == ["dhis2_chap_ping"]


def test_transitive_requires_are_pulled_in() -> None:
    """Asking for chap-core also runs ping and chap-route (its prereq chain)."""
    selected = resolve_checks(["dhis2_chap_core"])
    names = [c.name for c in selected]
    # Order is canonical (order, name): ping (10) -> chap-route (30) -> chap-core (40).
    assert names == ["dhis2_chap_ping", "dhis2_chap_route", "dhis2_chap_core"]


def test_unknown_name_raises() -> None:
    with pytest.raises(KeyError, match="unknown check"):
        resolve_checks(["does-not-exist"])


def test_empty_list_returns_empty() -> None:
    assert resolve_checks([]) == []
