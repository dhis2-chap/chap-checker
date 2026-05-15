"""Load chap-checker.toml into typed instance configs."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from chap_checker.checks.base import Status
from chap_checker.client import Dhis2Target
from chap_checker.logging import get_logger

if TYPE_CHECKING:
    from chap_checker.runner import TargetEntry

DEFAULT_CONFIG_FILENAME = "chap-checker.toml"

_log = get_logger("config")


class InstanceConfig(BaseModel):
    """One DHIS2 instance to check.

    Two auth modes are supported:

    - ``username`` + (``password`` or ``password_env``) - HTTP Basic.
    - (``token`` or ``token_env``) - DHIS2 Personal Access Token,
      sent as ``Authorization: ApiToken <value>``.

    Exactly one mode must be set per instance. ``username`` may also
    be supplied alongside a token for readability, but it isn't sent
    on the wire in token mode.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    username: str | None = None
    password: str | None = None
    password_env: str | None = None
    token: str | None = None
    token_env: str | None = None
    timeout_s: float = Field(default=10.0, gt=0)
    verify_tls: bool = True
    checks: list[str] | None = Field(default=None, min_length=1)
    alerts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_auth_mode(self) -> "InstanceConfig":
        has_password = self.password is not None or self.password_env is not None
        has_token = self.token is not None or self.token_env is not None
        if has_password and has_token:
            raise ValueError(
                "set either a password (password / password_env) or a token (token / token_env), not both.",
            )
        if not has_password and not has_token:
            raise ValueError(
                "set exactly one of 'password', 'password_env', 'token', or 'token_env'.",
            )
        if has_password:
            if self.password is not None and self.password_env is not None:
                raise ValueError("set exactly one of 'password' or 'password_env'")
            if self.username is None:
                raise ValueError("password / password_env requires 'username'.")
        elif self.token is not None and self.token_env is not None:
            raise ValueError("set exactly one of 'token' or 'token_env'")
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

    def resolve_token(self) -> str:
        """Return the PAT, reading from env if ``token_env`` is set."""
        if self.token is not None:
            return self.token
        assert self.token_env is not None  # guarded by validator
        value = os.environ.get(self.token_env)
        if value is None:
            raise RuntimeError(f"Environment variable '{self.token_env}' is not set.")
        return value

    def to_target(self) -> Dhis2Target:
        """Build a runtime :class:`Dhis2Target` from this entry."""
        if self.token is not None or self.token_env is not None:
            return Dhis2Target(
                base_url=self.url,
                token=self.resolve_token(),
                timeout_s=self.timeout_s,
                verify_tls=self.verify_tls,
            )
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


UiTheme = Literal["phosphor", "amber", "high", "tokyo", "dhis2"]
DEFAULT_UI_TITLE = "DHIS2 / Climate Instance Checker"


class UiConfig(BaseModel):
    """``[ui]`` section — branding and theme presented in both surfaces.

    ``title`` is the top-left header text shown in the Textual TUI and the
    web dashboard. ``theme`` selects the web color palette; the web
    palette also exposes it at runtime via the command palette, so the
    config value acts as the *starting default* — a per-user palette
    pick (persisted in browser storage) wins over the config until
    that override is cleared.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default=DEFAULT_UI_TITLE, min_length=1, max_length=120)
    theme: UiTheme = "phosphor"


DEFAULT_CONCURRENCY = 5


class CheckerConfig(BaseModel):
    """Top-level ``chap-checker.toml`` document."""

    model_config = ConfigDict(extra="forbid")

    instances: dict[str, InstanceConfig] = Field(default_factory=dict)
    alerts: AlertsConfig | None = None
    ui: UiConfig = Field(default_factory=UiConfig)
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
    if any(i.password is not None or i.token is not None for i in cfg.instances.values()):
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
