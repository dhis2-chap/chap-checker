import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from chap_checker.config import DEFAULT_UI_TITLE, CheckerConfig, InstanceConfig, UiConfig, load_config


@pytest.fixture(autouse=True)
def _propagate_chap_checker_logs() -> Iterator[None]:
    """Other tests may have called ``configure_logging`` (propagate=False);
    caplog captures at root, so re-enable propagation for this test file."""
    logger = logging.getLogger("chap_checker")
    prev = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = prev


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "chap-checker.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_single_instance_inline_password(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[instances.play]
url = "https://play.dhis2.org/40.0.0"
username = "admin"
password = "district"
""",
    )
    cfg = load_config(path)
    play = cfg.get("play")
    assert play.username == "admin"
    assert play.resolve_password() == "district"


def test_load_password_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MY_PASS", "secret")
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://example.test"
username = "u"
password_env = "MY_PASS"
""",
    )
    cfg = load_config(path)
    assert cfg.get("x").resolve_password() == "secret"


def test_password_env_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MY_PASS", raising=False)
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://example.test"
username = "u"
password_env = "MY_PASS"
""",
    )
    with pytest.raises(RuntimeError, match="MY_PASS"):
        load_config(path).get("x").resolve_password()


def test_both_password_sources_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        InstanceConfig(
            url="https://x.test",  # type: ignore[arg-type]
            username="u",
            password="p",
            password_env="E",
        )


def test_neither_password_source_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        InstanceConfig(url="https://x.test", username="u")  # type: ignore[arg-type]


def test_unknown_instance_lists_available() -> None:
    cfg = CheckerConfig(
        instances={
            "a": InstanceConfig(url="https://a.test", username="u", password="p"),  # type: ignore[arg-type]
            "b": InstanceConfig(url="https://b.test", username="u", password="p"),  # type: ignore[arg-type]
        }
    )
    with pytest.raises(KeyError, match="a, b"):
        cfg.get("c")


def test_zero_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        InstanceConfig(
            url="https://x.test",  # type: ignore[arg-type]
            username="u",
            password="p",
            timeout_s=0.0,
        )


def test_negative_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        InstanceConfig(
            url="https://x.test",  # type: ignore[arg-type]
            username="u",
            password="p",
            timeout_s=-1.0,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_warns_on_world_readable_with_inline_password(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    os.chmod(path, 0o644)

    with caplog.at_level(logging.WARNING, logger="chap_checker.config"):
        load_config(path)

    assert any("inline credentials" in r.message for r in caplog.records)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_no_warning_on_mode_600_with_inline_password(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    os.chmod(path, 0o600)

    with caplog.at_level(logging.WARNING, logger="chap_checker.config"):
        load_config(path)

    assert not any("inline credentials" in r.message for r in caplog.records)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
@pytest.mark.parametrize(
    ("name", "block"),
    [
        (
            "webhook_url",
            '[alerts.webhook]\nurl = "https://hooks.example/x"\n',
        ),
        (
            "webhook_headers",
            '[alerts.webhook]\nurl_env = "OK_ENV"\nheaders = { Authorization = "Bearer s3cret" }\n',
        ),
        (
            "auth_token",
            '[auth]\ntoken = "dev-secret-12345"\n',
        ),
    ],
)
def test_warns_on_world_readable_with_other_inline_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
    block: str,
) -> None:
    """Inline secrets beyond instance passwords also trigger the chmod-600 advisory."""
    monkeypatch.setenv("OK_ENV", "https://hooks.example/x")
    path = _write(
        tmp_path,
        f"""
[instances.x]
url = "https://x.test"
username = "u"
password_env = "X_PASS"

{block}
""",
    )
    monkeypatch.setenv("X_PASS", "p")
    os.chmod(path, 0o644)

    with caplog.at_level(logging.WARNING, logger="chap_checker.config"):
        load_config(path)

    assert any("inline credentials" in r.message for r in caplog.records), name


def test_unknown_check_name_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
checks = ["does_not_exist"]
""",
    )
    with pytest.raises(ValueError, match="unknown check name"):
        load_config(path)


def test_known_check_names_accepted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
checks = ["dhis2_ping"]
""",
    )
    cfg = load_config(path)
    assert cfg.get("x").checks == ["dhis2_ping"]


def test_instance_alerts_referencing_unconfigured_alerter_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
alerts = ["discord"]
""",
    )
    with pytest.raises(ValueError, match="unconfigured alerter"):
        load_config(path)


def test_instance_alerts_when_alerter_section_present_accepted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[alerts.slack]
webhook_url = "https://hooks.slack.com/x"

[instances.x]
url = "https://x.test"
username = "u"
password = "p"
alerts = ["slack"]
""",
    )
    cfg = load_config(path)
    assert cfg.get("x").alerts == ["slack"]


def test_instance_alerts_defaults_to_empty(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    cfg = load_config(path)
    assert cfg.get("x").alerts == []


def test_empty_check_list_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
checks = []
""",
    )
    with pytest.raises(ValueError, match="at least 1"):
        load_config(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_no_warning_on_world_readable_with_env_password_only(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password_env = "X_PASS"
""",
    )
    os.chmod(path, 0o644)

    with caplog.at_level(logging.WARNING, logger="chap_checker.config"):
        load_config(path)

    assert not any("inline credentials" in r.message for r in caplog.records)


# ---------- [ui] section ----------


def test_ui_defaults_when_section_omitted(tmp_path: Path) -> None:
    """Missing [ui] section yields the default title + phosphor theme."""
    path = _write(
        tmp_path,
        """
[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    cfg = load_config(path)
    assert cfg.ui.title == DEFAULT_UI_TITLE
    assert cfg.ui.theme == "phosphor"


def test_ui_title_and_theme_overridden(tmp_path: Path) -> None:
    """Explicit [ui] keys override the defaults."""
    path = _write(
        tmp_path,
        """
[ui]
title = "Operations"
theme = "amber"

[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    cfg = load_config(path)
    assert cfg.ui.title == "Operations"
    assert cfg.ui.theme == "amber"


@pytest.mark.parametrize("theme", ["phosphor", "amber", "high", "tokyo", "dhis2"])
def test_ui_theme_accepts_every_known_value(theme: str, tmp_path: Path) -> None:
    """Every theme the web palette ships must validate from the config side."""
    path = _write(
        tmp_path,
        f"""
[ui]
theme = "{theme}"

[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    cfg = load_config(path)
    assert cfg.ui.theme == theme


def test_ui_unknown_theme_rejected(tmp_path: Path) -> None:
    """A theme value outside the known set fails validation."""
    path = _write(
        tmp_path,
        """
[ui]
theme = "magenta"

[instances.x]
url = "https://x.test"
username = "u"
password = "p"
""",
    )
    with pytest.raises(ValueError, match="theme"):
        load_config(path)


def test_ui_empty_title_rejected() -> None:
    """An empty title violates min_length=1."""
    with pytest.raises(ValueError, match="at least 1"):
        UiConfig(title="")


def test_ui_unknown_field_rejected() -> None:
    """The [ui] section is extra=forbid — surprise keys fail loud."""
    with pytest.raises(ValueError, match="Extra inputs"):
        UiConfig(**{"title": "x", "theme": "phosphor", "logo": "/x.png"})  # type: ignore[arg-type]


def test_checker_config_ui_default_instance() -> None:
    """CheckerConfig instantiates a default UiConfig when not provided."""
    cfg = CheckerConfig(instances={})
    assert isinstance(cfg.ui, UiConfig)
    assert cfg.ui.title == DEFAULT_UI_TITLE
