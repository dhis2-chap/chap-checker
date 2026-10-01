"""`kind` on [instances.<key>]: config parsing, validation, and the CLI paths that care about it."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import typer
from pydantic import ValidationError
from typer.testing import CliRunner

from chap_checker.cli import _apply_check_override, _resolve_run_context, app
from chap_checker.client import Dhis2Target, OcsTarget
from chap_checker.config import CheckerConfig, Dhis2InstanceConfig, OcsInstanceConfig, load_config

_OCS = {"kind": "ocs", "url": "https://ocs.example"}
_DHIS2 = {"url": "https://dhis2.example", "username": "u", "password": "p"}


def _cfg(**instances: Mapping[str, object]) -> CheckerConfig:
    return CheckerConfig.model_validate({"instances": instances})


def test_missing_kind_is_dhis2() -> None:
    inst = _cfg(a=_DHIS2).instances["a"]
    assert isinstance(inst, Dhis2InstanceConfig)
    assert inst.kind == "dhis2"


def test_explicit_dhis2_kind() -> None:
    assert isinstance(_cfg(a={**_DHIS2, "kind": "dhis2"}).instances["a"], Dhis2InstanceConfig)


def test_ocs_needs_no_credentials() -> None:
    inst = _cfg(o=_OCS).instances["o"]
    assert isinstance(inst, OcsInstanceConfig)
    target = inst.to_target()
    assert isinstance(target, OcsTarget)
    assert target.kind == "ocs"
    assert str(target.base_url) == "https://ocs.example/"


def test_ocs_rejects_credentials() -> None:
    with pytest.raises(ValidationError, match="password"):
        _cfg(o={**_OCS, "password": "x"})


def test_ocs_rejects_retry_policy() -> None:
    with pytest.raises(ValidationError, match="retry_policy"):
        _cfg(o={**_OCS, "retry_policy": {"max_attempts": 3}})


def test_ocs_rejects_dhis2_checks() -> None:
    with pytest.raises(ValidationError, match="do not apply to kind 'ocs'"):
        _cfg(o={**_OCS, "checks": ["dhis2_ping"]})


def test_dhis2_rejects_ocs_checks() -> None:
    with pytest.raises(ValidationError, match="do not apply to kind 'dhis2'"):
        _cfg(a={**_DHIS2, "checks": ["ocs_health"]})


def test_shared_check_allowed_on_both_kinds() -> None:
    cfg = _cfg(a={**_DHIS2, "checks": ["http_2xx"]}, o={**_OCS, "checks": ["http_2xx", "ocs_info"]})
    assert cfg.instances["o"].checks == ["http_2xx", "ocs_info"]


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        _cfg(x={**_OCS, "kind": "geoserver"})


def test_ocs_has_no_inline_secret() -> None:
    assert not _cfg(o=_OCS).instances["o"].has_inline_secret()
    assert _cfg(a=_DHIS2).instances["a"].has_inline_secret()


def test_mixed_config_builds_matching_targets() -> None:
    cfg = _cfg(a=_DHIS2, o={**_OCS, "name": "Nepal"})
    entries = {name: inst.to_target_entry(name) for name, inst in cfg.instances.items()}
    assert isinstance(entries["a"].target, Dhis2Target)
    assert isinstance(entries["o"].target, OcsTarget)
    assert entries["o"].display_name == "Nepal"


def test_example_config_loads() -> None:
    cfg = load_config(Path(__file__).parent.parent / "chap-checker.toml.example")
    assert isinstance(cfg.instances["nepal-ocs"], OcsInstanceConfig)


def test_check_override_drops_instances_with_nothing_to_run(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _cfg(a=_DHIS2, o=_OCS)
    targets = [inst.to_target_entry(name) for name, inst in cfg.instances.items()]
    kept = _apply_check_override(targets, ["dhis2_ping"])
    assert [t.name for t in kept] == ["a"]
    assert "skipping instance 'o' (kind 'ocs')" in capsys.readouterr().err


def test_check_override_keeps_instances_with_a_shared_check() -> None:
    cfg = _cfg(a=_DHIS2, o=_OCS)
    targets = [inst.to_target_entry(name) for name, inst in cfg.instances.items()]
    kept = _apply_check_override(targets, ["http_2xx"])
    assert [t.name for t in kept] == ["a", "o"]
    assert all(t.check_names == ["http_2xx"] for t in kept)


def test_check_override_errors_when_nothing_applies(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('[instances.o]\nkind = "ocs"\nurl = "https://ocs.example"\n')
    with pytest.raises(typer.BadParameter, match="none of dhis2_ping apply"):
        _resolve_run_context(
            config=path,
            instance="o",
            url=None,
            username=None,
            password=None,
            password_env=None,
            token=None,
            token_env=None,
            timeout=10.0,
            insecure=False,
            check_names=["dhis2_ping"],
        )


def test_checks_list_json_includes_kinds() -> None:
    result = CliRunner().invoke(app, ["--json", "checks", "list"])
    assert result.exit_code == 0, result.output
    kinds = {c["name"]: c["kinds"] for c in json.loads(result.output)}
    assert kinds["http_2xx"] == ["dhis2", "ocs"]
    assert kinds["ocs_health"] == ["ocs"]
    assert kinds["dhis2_ping"] == ["dhis2"]
