from chap_checker.checks import all_checks


def test_registry_has_builtin_checks() -> None:
    names = {c.name for c in all_checks()}
    assert {
        "dhis2_ping",
        "dhis2_system_info",
        "dhis2_chap_route",
        "dhis2_chap_ping",
        "dhis2_chap_system_info",
        "dhis2_chap_modeling_app",
        "dhis2_chap_climate_app",
    } <= names


def test_check_protocol_fields() -> None:
    for check in all_checks():
        assert isinstance(check.name, str) and check.name
        assert isinstance(check.description, str) and check.description
        assert isinstance(check.order, int)
        assert isinstance(check.requires, list)
        assert all(isinstance(r, str) for r in check.requires)


def test_builtin_requires_reference_known_checks() -> None:
    names = {c.name for c in all_checks()}
    for check in all_checks():
        for req in check.requires:
            assert req in names, f"{check.name} requires unknown check {req!r}"


def test_builtin_checks_run_in_dependency_order() -> None:
    names = [c.name for c in all_checks()]
    expected = [
        "dhis2_ping",
        "dhis2_system_info",
        "dhis2_chap_route",
        "dhis2_chap_ping",
        "dhis2_chap_system_info",
        "dhis2_chap_modeling_app",
        "dhis2_chap_climate_app",
    ]
    assert names == expected
