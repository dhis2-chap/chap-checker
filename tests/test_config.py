import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from chap_checker.config import CheckerConfig, InstanceConfig, load_config


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
