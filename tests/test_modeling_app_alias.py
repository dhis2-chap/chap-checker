"""Regression tests for the modeling-app aliasing.

DHIS2 returns the App Hub UUID under either ``app_hub_id`` (snake_case) or
``appHubId`` (camelCase) depending on version / endpoint. Both must parse.
"""

from chap_checker.checks.dhis2_chap_modeling_app import Dhis2App


def test_parses_snake_case_app_hub_id() -> None:
    app = Dhis2App.model_validate({"app_hub_id": "abc123", "name": "Modeling"})
    assert app.app_hub_id == "abc123"


def test_parses_camel_case_app_hub_id() -> None:
    app = Dhis2App.model_validate({"appHubId": "abc123", "name": "Modeling"})
    assert app.app_hub_id == "abc123"


def test_parses_camel_case_app_type_and_launch_url() -> None:
    app = Dhis2App.model_validate(
        {
            "appHubId": "abc123",
            "appType": "APP",
            "launchUrl": "https://example.test/index.html",
        }
    )
    assert app.app_type == "APP"
    assert app.launch_url == "https://example.test/index.html"
