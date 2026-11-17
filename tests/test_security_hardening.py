"""Regression tests for the auth / secret-handling hardening.

Covers:

- empty, whitespace-padded and non-ASCII bearer tokens rejected at config
  load (literal `token`) and at resolve time (`token_env`), on startup and
  on reload;
- `require_auth` never authenticating an empty `Bearer ` header and never
  raising (500) on a non-ASCII client token;
- empty env values rejected for every `*_env` secret;
- config errors (reload endpoint, CLI) never echoing rejected secret values;
- `user:pass@` userinfo rejected in instance and alerter URLs.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, ValidationError
from typer.testing import CliRunner

from chap_checker.cli import app as cli_app
from chap_checker.client import Dhis2Target
from chap_checker.config import (
    AuthConfig,
    CheckerConfig,
    Dhis2InstanceConfig,
    OgsInstanceConfig,
    SlackAlertConfig,
    WebhookAlertConfig,
    format_config_error,
    load_config,
    validate_bearer_token,
)
from chap_checker.daemon import DashboardServer
from chap_checker.runner import TargetEntry
from chap_checker.serve import make_app

runner = CliRunner()

_SECRET = "hunter2-do-not-leak"

_VALID_TOML = """\
[instances.alpha]
url = "https://alpha.example"
username = "u"
password = "p"
"""


def _server(config_path: Path | None = None) -> DashboardServer:
    return DashboardServer(
        targets=[
            TargetEntry(
                name="prod",
                target=Dhis2Target(base_url=cast(HttpUrl, "https://prod.example"), username="u", password="p"),
            )
        ],
        cfg=CheckerConfig(
            instances={
                "prod": Dhis2InstanceConfig(url=cast(HttpUrl, "https://prod.example"), username="u", password="p"),
            },
        ),
        state_path=None,
        interval_s=30.0,
        alerts_enabled=False,
        config_path=config_path,
    )


# ---------- bearer-token validation ----------


@pytest.mark.parametrize("token", ["abc", "s3cret", "A-Za-z0-9._~+/=", "!#$%&'()*+,-./:;<=>?@[]^_`{|}~"])
def test_validate_bearer_token_accepts_printable_ascii(token: str) -> None:
    assert validate_bearer_token(token, source_label="t") == token


@pytest.mark.parametrize(
    ("token", "fragment"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        (" abc", "leading or trailing whitespace"),
        ("abc\n", "leading or trailing whitespace"),
        ("a b", "printable ASCII"),
        ("tøken", "printable ASCII"),
        ("tok\x00en", "printable ASCII"),
    ],
)
def test_validate_bearer_token_rejects(token: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment) as info:
        validate_bearer_token(token, source_label="auth.token")
    if token.strip():
        assert token.strip() not in str(info.value)


@pytest.mark.parametrize("token", ["", "  ", " padded", "tøken", "has space"])
def test_auth_literal_token_rejected_at_model_build(token: str) -> None:
    with pytest.raises(ValidationError):
        AuthConfig(token=token)


def test_non_ascii_configured_token_rejected_at_config_load(tmp_path: Path) -> None:
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken = "tøken"\n', encoding="utf-8")
    with pytest.raises(ValidationError, match="printable ASCII"):
        load_config(cfg_path)


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_auth_empty_env_token_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CHAP_TEST_TOKEN", value)
    with pytest.raises(RuntimeError, match="set but empty"):
        AuthConfig(token_env="CHAP_TEST_TOKEN").resolve_token()


@pytest.mark.parametrize("value", [" padded", "tøken", "has space"])
def test_auth_invalid_env_token_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CHAP_TEST_TOKEN", value)
    with pytest.raises(RuntimeError, match="CHAP_TEST_TOKEN") as info:
        AuthConfig(token_env="CHAP_TEST_TOKEN").resolve_token()
    assert value.strip() not in str(info.value)


def test_auth_valid_env_token_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAP_TEST_TOKEN", "0123abcd")
    assert AuthConfig(token_env="CHAP_TEST_TOKEN").resolve_token() == "0123abcd"


def test_make_app_rejects_empty_auth_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        make_app(_server(), auth_token="")


def test_serve_cli_refuses_empty_env_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`serve` exits with a clean error, not an open daemon, when the token env var is empty."""
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken_env = "CHAP_TEST_TOKEN"\n')
    monkeypatch.setenv("CHAP_TEST_TOKEN", "")
    result = runner.invoke(cli_app, ["serve", "--config", str(cfg_path)])
    assert result.exit_code != 0
    assert "set but empty" in result.output


def test_reload_with_empty_env_token_keeps_previous_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML + '\n[auth]\ntoken_env = "CHAP_TEST_TOKEN"\n')
    monkeypatch.setenv("CHAP_TEST_TOKEN", "")
    server = _server(cfg_path)
    app = make_app(server, auth_token="old-token")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/reload", headers={"Authorization": "Bearer old-token"})
        assert r.status_code == 400
        assert "set but empty" in r.json()["message"]
        assert server.resolved_auth_token == "old-token"
        assert client.get("/api/state", headers={"Authorization": "Bearer "}).status_code == 401
        assert client.get("/api/state", headers={"Authorization": "Bearer old-token"}).status_code == 200


# ---------- require_auth ----------


@pytest.mark.parametrize("header", ["Bearer ", "Bearer    ", "Bearer", "bearer s3cret", "Basic s3cret"])
def test_require_auth_rejects_empty_or_malformed_header(header: str) -> None:
    app = make_app(_server(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/state", headers={"Authorization": header}).status_code == 401


def test_require_auth_rejects_empty_header_even_if_token_state_is_empty() -> None:
    """Defence in depth: a server whose token state was forced to "" still refuses `Bearer `."""
    server = _server()
    app = make_app(server, auth_token="s3cret")
    server.resolved_auth_token = ""
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/state", headers={"Authorization": "Bearer "}).status_code == 401


@pytest.mark.parametrize("provided", ["tøken", "s3creté", "☃"])
def test_require_auth_non_ascii_client_token_is_401_not_500(provided: str) -> None:
    app = make_app(_server(), auth_token="s3cret")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/state", headers={b"Authorization": f"Bearer {provided}".encode()})
        assert r.status_code == 401


# ---------- empty env secrets ----------


def test_dhis2_password_env_empty_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAP_TEST_PW", "")
    inst = Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), username="u", password_env="CHAP_TEST_PW")
    with pytest.raises(RuntimeError, match="set but empty"):
        inst.resolve_password()


def test_dhis2_token_env_empty_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAP_TEST_PAT", " ")
    inst = Dhis2InstanceConfig(url=cast(HttpUrl, "https://x.example"), token_env="CHAP_TEST_PAT")
    with pytest.raises(RuntimeError, match="set but empty"):
        inst.resolve_token()


@pytest.mark.parametrize("field", ["password", "token"])
def test_dhis2_literal_empty_secret_rejected(field: str) -> None:
    kwargs: dict[str, str] = {"url": "https://x.example", field: ""}
    if field == "password":
        kwargs["username"] = "u"
    with pytest.raises(ValidationError, match="must not be empty"):
        Dhis2InstanceConfig.model_validate(kwargs)


def test_slack_webhook_env_empty_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAP_TEST_SLACK", "")
    with pytest.raises(RuntimeError, match="set but empty"):
        SlackAlertConfig(webhook_url_env="CHAP_TEST_SLACK").resolve_webhook_url()


def test_webhook_url_env_empty_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAP_TEST_HOOK", "")
    with pytest.raises(RuntimeError, match="set but empty"):
        WebhookAlertConfig(url_env="CHAP_TEST_HOOK").resolve_url()


# ---------- secret-free error messages ----------


_LEAKY_CONFIGS = [
    # Both auth modes set: the model-level error's input is the whole block.
    f'[instances.a]\nurl = "https://a.example"\nusername = "u"\npassword = "{_SECRET}"\ntoken = "{_SECRET}"\n',
    # Invalid bearer token: the field error's input is the token itself.
    _VALID_TOML + f'\n[auth]\ntoken = "{_SECRET} with space"\n',
    # Userinfo in the URL: the field error's input is the URL.
    f'[instances.a]\nurl = "https://u:{_SECRET}@a.example"\nusername = "u"\npassword = "p"\n',
    # Unknown key next to a secret: extra_forbidden echoes the value.
    f'[instances.a]\nurl = "https://a.example"\nusername = "u"\npassword = "p"\npasword = "{_SECRET}"\n',
]


@pytest.mark.parametrize("toml_text", _LEAKY_CONFIGS)
def test_format_config_error_drops_input_values(toml_text: str) -> None:
    with pytest.raises(ValidationError) as info:
        CheckerConfig.model_validate(tomllib.loads(toml_text))
    assert _SECRET in str(info.value)  # pydantic's default string does leak it
    message = format_config_error(info.value)
    assert _SECRET not in message
    assert "instances" in message or "auth" in message


def test_format_config_error_withholds_unknown_exception_text() -> None:
    message = format_config_error(KeyError(_SECRET))
    assert _SECRET not in message
    assert "KeyError" in message


@pytest.mark.parametrize("toml_text", _LEAKY_CONFIGS)
def test_reload_endpoint_does_not_echo_secrets(tmp_path: Path, toml_text: str) -> None:
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(_VALID_TOML)
    server = _server(cfg_path)
    app = make_app(server)
    with TestClient(app, raise_server_exceptions=False) as client:
        cfg_path.write_text(toml_text)
        r = client.post("/api/reload")
        assert r.status_code == 400
        assert r.json()["status"] == "error"
        assert r.json()["message"]
        assert _SECRET not in r.text


@pytest.mark.parametrize("toml_text", _LEAKY_CONFIGS)
def test_cli_config_error_does_not_echo_secrets(tmp_path: Path, toml_text: str) -> None:
    cfg_path = tmp_path / "chap-checker.toml"
    cfg_path.write_text(toml_text)
    result = runner.invoke(cli_app, ["verify", "--config", str(cfg_path)])
    assert result.exit_code != 0
    assert "invalid config" in result.output
    assert _SECRET not in result.output
    assert result.exception is None or _SECRET not in str(result.exception)


# ---------- userinfo in URLs ----------


@pytest.mark.parametrize("url", [f"https://user:{_SECRET}@x.example", "https://user@x.example"])
def test_instance_url_with_userinfo_rejected(url: str) -> None:
    with pytest.raises(ValidationError, match="credentials embedded in the URL"):
        Dhis2InstanceConfig.model_validate({"url": url, "username": "u", "password": "p"})
    with pytest.raises(ValidationError, match="credentials embedded in the URL"):
        OgsInstanceConfig.model_validate({"url": url, "kind": "ogs"})


def test_slack_webhook_url_with_userinfo_rejected() -> None:
    with pytest.raises(ValidationError, match="credentials embedded in the URL"):
        SlackAlertConfig.model_validate({"webhook_url": f"https://u:{_SECRET}@hooks.slack.com/services/T/B/X"})


def test_webhook_url_with_userinfo_rejected_but_path_and_query_secrets_allowed() -> None:
    with pytest.raises(ValidationError, match="credentials embedded in the URL"):
        WebhookAlertConfig.model_validate({"url": f"https://u:{_SECRET}@hooks.example/x"})
    ok = WebhookAlertConfig.model_validate({"url": f"https://hooks.example/{_SECRET}?key={_SECRET}"})
    assert _SECRET in ok.resolve_url()


def test_webhook_url_env_with_userinfo_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAP_TEST_HOOK", f"https://u:{_SECRET}@hooks.example/x")
    with pytest.raises(RuntimeError, match="credentials embedded in the URL") as info:
        WebhookAlertConfig(url_env="CHAP_TEST_HOOK").resolve_url()
    assert _SECRET not in str(info.value)
