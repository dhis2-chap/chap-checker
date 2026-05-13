from chap_checker.checks import all_checks


def test_registry_has_builtin_checks() -> None:
    names = {c.name for c in all_checks()}
    assert {"ping", "system-info", "chap-route", "chap-core", "modeling-app"} <= names


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
    expected = ["ping", "system-info", "chap-route", "chap-core", "modeling-app"]
    assert names == expected
