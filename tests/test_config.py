from pathlib import Path

import pytest

from chap_checker.config import CheckerConfig, InstanceConfig, load_config


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
