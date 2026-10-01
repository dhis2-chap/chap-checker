import pytest

from chap_checker.checks import all_checks, resolve_checks


def test_none_returns_every_check_for_the_kind() -> None:
    dhis2 = {c.name for c in resolve_checks(None)}
    ocs = {c.name for c in resolve_checks(None, kind="ocs")}
    assert dhis2 | ocs == {c.name for c in all_checks()}
    assert dhis2 & ocs == {"http_2xx"}
    assert {c.name for c in resolve_checks(None, kind="dhis2")} == dhis2


def test_names_for_another_kind_are_dropped() -> None:
    assert [c.name for c in resolve_checks(["dhis2_ping", "ocs_info"], kind="ocs")] == ["ocs_health", "ocs_info"]
    assert [c.name for c in resolve_checks(["dhis2_ping", "ocs_info"], kind="dhis2")] == ["dhis2_ping"]


def test_only_other_kind_names_resolve_to_empty() -> None:
    assert resolve_checks(["dhis2_system_info"], kind="ocs") == []


def test_shared_check_resolves_for_both_kinds() -> None:
    assert [c.name for c in resolve_checks(["http_2xx"], kind="ocs")] == ["http_2xx"]
    assert [c.name for c in resolve_checks(["http_2xx"], kind="dhis2")] == ["http_2xx"]


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
