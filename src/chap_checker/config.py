"""Load chap-checker.toml into typed instance configs."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from chap_checker.checks.base import Status
from chap_checker.client import Dhis2Target
from chap_checker.logging import get_logger

if TYPE_CHECKING:
    from chap_checker.runner import TargetEntry

DEFAULT_CONFIG_FILENAME = "chap-checker.toml"

_log = get_logger("config")


class InstanceConfig(BaseModel):
    """One DHIS2 instance to check."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    username: str
    password: str | None = None
    password_env: str | None = None
    timeout_s: float = Field(default=10.0, gt=0)
    verify_tls: bool = True
    checks: list[str] | None = Field(default=None, min_length=1)
    alerts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_password_source(self) -> "InstanceConfig":
        if (self.password is None) == (self.password_env is None):
            raise ValueError("set exactly one of 'password' or 'password_env'")
        return self

    @model_validator(mode="after")
    def _checks_must_exist(self) -> "InstanceConfig":
        if self.checks is None:
            return self
        # Late import: triggers chap_checker.checks.__init__ which registers
        # every built-in check, populating the registry we validate against.
        from chap_checker.checks.base import all_checks

        known = {c.name for c in all_checks()}
        unknown = [c for c in self.checks if c not in known]
        if unknown:
            raise ValueError(f"unknown check name(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}")
        return self

    def resolve_password(self) -> str:
        """Return the password, reading from env if ``password_env`` is set."""
        if self.password is not None:
            return self.password
        assert self.password_env is not None  # guarded by validator
        value = os.environ.get(self.password_env)
        if value is None:
            raise RuntimeError(f"Environment variable '{self.password_env}' is not set.")
        return value

    def to_target(self) -> Dhis2Target:
        """Build a runtime :class:`Dhis2Target` from this entry."""
        return Dhis2Target(
            base_url=self.url,
            username=self.username,
            password=self.resolve_password(),
            timeout_s=self.timeout_s,
            verify_tls=self.verify_tls,
        )

    def to_target_entry(self, name: str) -> "TargetEntry":
        """Build a runtime :class:`TargetEntry` (with optional check filter and opt-in alerters)."""
        from chap_checker.runner import TargetEntry

        return TargetEntry(
            name=name,
            target=self.to_target(),
            check_names=self.checks,
            alerts=list(self.alerts),
        )


class SlackAlertConfig(BaseModel):
    """Slack Incoming Webhook alerter configuration."""

    model_config = ConfigDict(extra="forbid")

    webhook_url: HttpUrl | None = None
    webhook_url_env: str | None = None
    notify_on: list[Status] = Field(default_factory=lambda: [Status.FAIL, Status.ERROR, Status.WARN])
    timeout_s: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def _exactly_one_webhook_source(self) -> "SlackAlertConfig":
        if (self.webhook_url is None) == (self.webhook_url_env is None):
            raise ValueError("set exactly one of 'webhook_url' or 'webhook_url_env'")
        return self

    def resolve_webhook_url(self) -> str:
        """Return the webhook URL, reading from env if ``webhook_url_env`` is set."""
        if self.webhook_url is not None:
            return str(self.webhook_url)
        assert self.webhook_url_env is not None  # guarded by validator
        value = os.environ.get(self.webhook_url_env)
        if value is None:
            raise RuntimeError(f"Environment variable '{self.webhook_url_env}' is not set.")
        return value


class AlertsConfig(BaseModel):
    """``[alerts.*]`` section — one optional sub-section per alerter."""

    model_config = ConfigDict(extra="forbid")

    slack: SlackAlertConfig | None = None


DEFAULT_CONCURRENCY = 5


class CheckerConfig(BaseModel):
    """Top-level ``chap-checker.toml`` document."""

    model_config = ConfigDict(extra="forbid")

    instances: dict[str, InstanceConfig] = Field(default_factory=dict)
    alerts: AlertsConfig | None = None
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, gt=0, le=100)

    def get(self, name: str) -> InstanceConfig:
        """Return one instance by name or raise :class:`KeyError`."""
        if name not in self.instances:
            available = ", ".join(sorted(self.instances)) or "<none>"
            raise KeyError(f"Unknown instance '{name}'. Available: {available}")
        return self.instances[name]

    def _configured_alerter_names(self) -> set[str]:
        """Names of every ``[alerts.<name>]`` section actually present."""
        if self.alerts is None:
            return set()
        configured: set[str] = set()
        if self.alerts.slack is not None:
            configured.add("slack")
        return configured

    @model_validator(mode="after")
    def _instance_alerts_must_match_configured(self) -> "CheckerConfig":
        configured = self._configured_alerter_names()
        for name, inst in self.instances.items():
            unknown = [a for a in inst.alerts if a not in configured]
            if unknown:
                raise ValueError(
                    f"instance '{name}' references unconfigured alerter(s): "
                    f"{', '.join(unknown)}. "
                    f"Configured alerters: {', '.join(sorted(configured)) or '<none>'}"
                )
        return self


def default_config_path() -> Path:
    """Path the CLI looks at when no ``--config`` is given: ``./chap-checker.toml``."""
    return Path.cwd() / DEFAULT_CONFIG_FILENAME


def load_config(path: Path) -> CheckerConfig:
    """Parse a TOML config file into a :class:`CheckerConfig`."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    cfg = CheckerConfig.model_validate(data)
    _warn_if_insecure_permissions(path, cfg)
    return cfg


def _has_inline_secret(cfg: CheckerConfig) -> bool:
    if any(i.password is not None for i in cfg.instances.values()):
        return True
    if cfg.alerts is not None and cfg.alerts.slack is not None and cfg.alerts.slack.webhook_url is not None:
        return True
    return False


def _warn_if_insecure_permissions(path: Path, cfg: CheckerConfig) -> None:
    """Log a warning when ``path`` is group- or world-readable AND carries inline secrets.

    Webhook URLs and inline passwords are credentials; the file should be
    ``chmod 0600``. Skipped on non-POSIX where the bits don't apply.
    """
    if os.name != "posix":
        return
    if not _has_inline_secret(cfg):
        return
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode & 0o077:
        _log.warning(
            "%s contains inline credentials and is mode %o; recommend `chmod 600 %s`.",
            path,
            mode,
            path,
        )
