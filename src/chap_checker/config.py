"""Load chap-checker.toml into typed instance configs."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from dhis2w_client import RetryPolicy
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    HttpUrl,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from chap_checker.checks.base import Status
from chap_checker.client import BaseTarget, Dhis2Target, OgsTarget
from chap_checker.logging import get_logger

if TYPE_CHECKING:
    from chap_checker.runner import TargetEntry

DEFAULT_CONFIG_FILENAME = "chap-checker.toml"

_log = get_logger("config")


def _resolve_value_or_env(value: str | None, env_var: str | None, *, source_label: str) -> str:
    """Return `value` if set, otherwise read `env_var` from the environment.

    The "one of literal or env var" pattern repeats across `Dhis2InstanceConfig`,
    `SlackAlertConfig`, `WebhookAlertConfig`, and `AuthConfig`. Centralising
    it here keeps the error message shape consistent and avoids the
    pre-condition `assert` dance each caller used to do.

    Every caller resolves a secret (password, token, webhook URL), and an
    empty secret is never valid, so an env var that is set but empty or
    whitespace-only is rejected rather than silently passed through.

    `source_label` names the field for error messages
    (e.g. `"password_env"`, `"auth.token_env"`).
    """
    if value is not None:
        return value
    if env_var is None:
        raise RuntimeError(f"{source_label}: neither a literal value nor an env-var name was set.")
    resolved = os.environ.get(env_var)
    if resolved is None:
        raise RuntimeError(f"Environment variable '{env_var}' is not set (referenced by {source_label}).")
    if not resolved.strip():
        raise RuntimeError(f"Environment variable '{env_var}' is set but empty (referenced by {source_label}).")
    return resolved


# RFC 6750 allows a narrower b64token alphabet; we accept any printable,
# non-space ASCII so hex / base64 / urlsafe tokens all work while keeping
# the value comparable as bytes and safe to put in an HTTP header.
_BEARER_TOKEN_RE = re.compile(r"[\x21-\x7e]+")


def validate_bearer_token(token: str, *, source_label: str) -> str:
    """Return `token` unchanged if it is usable as a bearer token, else raise `ValueError`.

    Rejects empty values, surrounding whitespace, and anything outside
    printable ASCII without spaces. The message never echoes the token.
    """
    if not token.strip():
        raise ValueError(f"{source_label}: bearer token must not be empty.")
    if token != token.strip():
        raise ValueError(f"{source_label}: bearer token must not have leading or trailing whitespace.")
    if _BEARER_TOKEN_RE.fullmatch(token) is None:
        raise ValueError(
            f"{source_label}: bearer token may only contain printable ASCII characters without spaces "
            "(generate one with `openssl rand -hex 32`).",
        )
    return token


_USERINFO_MESSAGE = (
    "credentials embedded in the URL (https://user:pass@host) are not allowed; "
    "they would be echoed in reports, /api/state and alerts."
)


def _reject_url_userinfo(url: str, *, hint: str) -> None:
    """Raise `ValueError` if `url` carries a `user[:pass]@` component.

    The message never includes the URL itself, since the userinfo is the secret.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return  # malformed URLs are reported by the URL type itself
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{_USERINFO_MESSAGE} {hint}")


def format_config_error(exc: BaseException) -> str:
    """Return an operator-facing description of a config load / reload failure without secrets.

    `pydantic.ValidationError`'s default string includes every rejected
    `input_value`, which can be a password, token or webhook URL. This
    renders only the location and message of each error. TOML parse
    errors, missing files and our own `RuntimeError`s carry no secret
    values (they name env vars and paths, never their contents), so their
    text is kept. Anything else is reported by type only.
    """
    if isinstance(exc, ValidationError):
        lines = [f"{exc.error_count()} validation error(s) for {exc.title}:"]
        for err in exc.errors(include_url=False, include_input=False, include_context=False):
            loc = ".".join(str(part) for part in err["loc"]) or "<root>"
            lines.append(f"  {loc}: {err['msg']}")
        return "\n".join(lines)
    if isinstance(exc, (tomllib.TOMLDecodeError, OSError, RuntimeError)):
        return str(exc)
    return f"{type(exc).__name__} while loading config (details withheld)."


class BaseInstanceConfig(BaseModel):
    """Fields every ``[instances.<key>]`` block shares, whatever its ``kind``."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str | None = None
    url: HttpUrl
    timeout_s: float = Field(default=10.0, gt=0)
    verify_tls: bool = True
    checks: list[str] | None = Field(default=None, min_length=1)
    alerts: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url_without_credentials(cls, value: HttpUrl) -> HttpUrl:
        _reject_url_userinfo(
            str(value),
            hint="Use the 'username' + 'password' / 'password_env' or 'token' / 'token_env' fields instead.",
        )
        return value

    @model_validator(mode="after")
    def _checks_must_exist(self) -> Self:
        if self.checks is None:
            return self
        # Late import: triggers chap_checker.checks.__init__ which registers
        # every built-in check, populating the registry we validate against.
        from chap_checker.checks.base import all_checks, check_kinds

        kind: str = getattr(self, "kind")
        by_name = {c.name: c for c in all_checks()}
        unknown = [c for c in self.checks if c not in by_name]
        if unknown:
            raise ValueError(f"unknown check name(s): {', '.join(unknown)}. Known: {', '.join(sorted(by_name))}")
        wrong_kind = [c for c in self.checks if kind not in check_kinds(by_name[c])]
        if wrong_kind:
            applicable = sorted(n for n, c in by_name.items() if kind in check_kinds(c))
            raise ValueError(
                f"check(s) {', '.join(wrong_kind)} do not apply to kind '{kind}'. "
                f"Checks for '{kind}': {', '.join(applicable)}"
            )
        return self

    def has_inline_secret(self) -> bool:
        """Return True if this block carries a credential inline in the TOML."""
        return False

    def to_target(self, *, default_retry_policy: RetryPolicy | None = None) -> BaseTarget:
        """Build the runtime target for this block."""
        raise NotImplementedError

    def to_target_entry(
        self,
        name: str,
        *,
        default_retry_policy: RetryPolicy | None = None,
    ) -> "TargetEntry":
        """Build a runtime :class:`TargetEntry` (with optional check filter and opt-in alerters)."""
        from chap_checker.runner import TargetEntry

        return TargetEntry(
            name=name,
            display_name=self.name,
            target=self.to_target(default_retry_policy=default_retry_policy),
            check_names=self.checks,
            alerts=list(self.alerts),
        )


class Dhis2InstanceConfig(BaseInstanceConfig):
    """One DHIS2 instance to check (``kind = "dhis2"``, the default).

    Two auth modes are supported:

    - ``username`` + (``password`` or ``password_env``) - HTTP Basic.
    - (``token`` or ``token_env``) - DHIS2 Personal Access Token,
      sent as ``Authorization: ApiToken <value>``.

    Exactly one mode must be set per instance. ``username`` may also
    be supplied alongside a token for readability, but it isn't sent
    on the wire in token mode.
    """

    kind: Literal["dhis2"] = "dhis2"
    username: str | None = None
    password: str | None = None
    password_env: str | None = None
    token: str | None = None
    token_env: str | None = None
    retry_policy: RetryPolicy | None = None

    @field_validator("password", "token")
    @classmethod
    def _literal_secret_not_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be empty; remove the field or set a real value.")
        return value

    @model_validator(mode="after")
    def _exactly_one_auth_mode(self) -> "Dhis2InstanceConfig":
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

    def has_inline_secret(self) -> bool:
        """Return True if a password or token is written inline."""
        return self.password is not None or self.token is not None

    def resolve_password(self) -> str:
        """Return the password, reading from env if ``password_env`` is set."""
        return _resolve_value_or_env(self.password, self.password_env, source_label="password_env")

    def resolve_token(self) -> str:
        """Return the PAT, reading from env if ``token_env`` is set."""
        return _resolve_value_or_env(self.token, self.token_env, source_label="token_env")

    def to_target(self, *, default_retry_policy: RetryPolicy | None = None) -> Dhis2Target:
        """Build a runtime :class:`Dhis2Target` from this entry.

        `default_retry_policy` is the top-level `[retry]` block, applied
        when the per-instance config doesn't override it. The
        per-instance `retry_policy` always wins when set.
        """
        retry_policy = self.retry_policy if self.retry_policy is not None else default_retry_policy
        if self.token is not None or self.token_env is not None:
            return Dhis2Target(
                base_url=self.url,
                token=self.resolve_token(),
                timeout_s=self.timeout_s,
                verify_tls=self.verify_tls,
                retry_policy=retry_policy,
            )
        return Dhis2Target(
            base_url=self.url,
            username=self.username,
            password=self.resolve_password(),
            timeout_s=self.timeout_s,
            verify_tls=self.verify_tls,
            retry_policy=retry_policy,
        )


class OgsInstanceConfig(BaseInstanceConfig):
    """One Open Geo Service deployment to check (``kind = "ogs"``).

    OGS has no authentication, so there are no credential fields; setting
    one is a validation error. The top-level ``[retry]`` block does not
    apply to this kind.
    """

    kind: Literal["ogs"]

    def to_target(self, *, default_retry_policy: RetryPolicy | None = None) -> OgsTarget:  # noqa: ARG002
        """Build a runtime :class:`OgsTarget` from this entry."""
        return OgsTarget(base_url=self.url, timeout_s=self.timeout_s, verify_tls=self.verify_tls)


def _instance_kind(value: Any) -> str:
    """Discriminate ``[instances.<key>]`` blocks on ``kind``, defaulting to DHIS2."""
    if isinstance(value, dict):
        return str(value.get("kind", "dhis2"))
    return str(getattr(value, "kind", "dhis2"))


InstanceConfig = Annotated[
    Annotated[Dhis2InstanceConfig, Tag("dhis2")] | Annotated[OgsInstanceConfig, Tag("ogs")],
    Discriminator(_instance_kind),
]
"""Any ``[instances.<key>]`` block. ``kind`` picks the variant; omitted means ``"dhis2"``."""


_SLACK_USERINFO_HINT = "Slack incoming-webhook URLs never need them."


class SlackAlertConfig(BaseModel):
    """Slack Incoming Webhook alerter configuration."""

    model_config = ConfigDict(extra="forbid")

    webhook_url: HttpUrl | None = None
    webhook_url_env: str | None = None
    notify_on: list[Status] = Field(default_factory=lambda: [Status.FAIL, Status.ERROR, Status.WARN])
    timeout_s: float = Field(default=10.0, gt=0)

    @field_validator("webhook_url")
    @classmethod
    def _url_without_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None:
            _reject_url_userinfo(str(value), hint=_SLACK_USERINFO_HINT)
        return value

    @model_validator(mode="after")
    def _exactly_one_webhook_source(self) -> "SlackAlertConfig":
        if (self.webhook_url is None) == (self.webhook_url_env is None):
            raise ValueError("set exactly one of 'webhook_url' or 'webhook_url_env'")
        return self

    def resolve_webhook_url(self) -> str:
        """Return the webhook URL, reading from env if ``webhook_url_env`` is set."""
        literal = str(self.webhook_url) if self.webhook_url is not None else None
        value = _resolve_value_or_env(literal, self.webhook_url_env, source_label="alerts.slack.webhook_url_env")
        try:
            _reject_url_userinfo(value, hint=_SLACK_USERINFO_HINT)
        except ValueError as exc:
            raise RuntimeError(f"alerts.slack.webhook_url_env: {exc}") from None
        return value


_WEBHOOK_USERINFO_HINT = (
    "Send credentials with the 'headers' table (e.g. Authorization); "
    "secrets in the URL path or query string are still accepted."
)


class WebhookAlertConfig(BaseModel):
    """Generic HTTP-webhook alerter configuration.

    Posts a canonical JSON envelope (see
    :class:`chap_checker.alerts.webhook.WebhookAlerter`) to any URL that
    accepts an ``application/json`` POST. Use this to wire chap-checker
    into receivers that aren't (yet) covered by a dedicated alerter -
    PagerDuty Events, OpsGenie, an internal incident bus, etc.

    Auth: pass any required tokens via the ``headers`` dict. Values are
    literal - put them in a chmod-600 config alongside the rest of your
    credentials. A future iteration will add per-header env-var
    substitution; for now keep the file off-disk-shareable.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl | None = None
    url_env: str | None = None
    notify_on: list[Status] = Field(default_factory=lambda: [Status.FAIL, Status.ERROR, Status.WARN])
    timeout_s: float = Field(default=10.0, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url_without_credentials(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None:
            _reject_url_userinfo(str(value), hint=_WEBHOOK_USERINFO_HINT)
        return value

    @model_validator(mode="after")
    def _exactly_one_url_source(self) -> "WebhookAlertConfig":
        if (self.url is None) == (self.url_env is None):
            raise ValueError("set exactly one of 'url' or 'url_env'")
        return self

    def resolve_url(self) -> str:
        """Return the webhook URL, reading from env if ``url_env`` is set."""
        literal = str(self.url) if self.url is not None else None
        value = _resolve_value_or_env(literal, self.url_env, source_label="alerts.webhook.url_env")
        try:
            _reject_url_userinfo(value, hint=_WEBHOOK_USERINFO_HINT)
        except ValueError as exc:
            raise RuntimeError(f"alerts.webhook.url_env: {exc}") from None
        return value


class AlertsConfig(BaseModel):
    """``[alerts.*]`` section — one optional sub-section per alerter."""

    model_config = ConfigDict(extra="forbid")

    slack: SlackAlertConfig | None = None
    webhook: WebhookAlertConfig | None = None


class AuthConfig(BaseModel):
    """`[auth]` section — protects `chap-checker serve`'s `/api/*` routes.

    Presence of the block in the TOML opts the daemon into requiring an
    `Authorization: Bearer <token>` header on every protected request.
    Absent means auth is disabled (today's behaviour).

    Comparison uses `hmac.compare_digest` server-side so a wrong token
    leaks no length / prefix information. Rotation is "update the env
    var, restart serve".
    """

    model_config = ConfigDict(extra="forbid")

    token: str | None = Field(default=None, repr=False)
    token_env: str | None = None

    @field_validator("token")
    @classmethod
    def _token_is_valid_bearer(cls, value: str | None) -> str | None:
        if value is not None:
            validate_bearer_token(value, source_label="auth.token")
        return value

    @model_validator(mode="after")
    def _exactly_one_token_source(self) -> "AuthConfig":
        if (self.token is None) == (self.token_env is None):
            raise ValueError(
                "set exactly one of 'token' or 'token_env' in [auth] "
                "(or omit the [auth] block entirely to disable auth).",
            )
        return self

    def resolve_token(self) -> str:
        """Return the bearer token, reading from env if `token_env` is set.

        Raises `RuntimeError` when the env var is missing, empty, or holds
        a value that is not a valid bearer token (see
        :func:`validate_bearer_token`). A literal `token` is already
        validated when the model is built.
        """
        value = _resolve_value_or_env(self.token, self.token_env, source_label="auth.token_env")
        if self.token is None:
            try:
                validate_bearer_token(value, source_label=f"auth.token_env ({self.token_env})")
            except ValueError as exc:
                raise RuntimeError(str(exc)) from None
        return value


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

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    instances: dict[str, InstanceConfig] = Field(default_factory=dict)
    alerts: AlertsConfig | None = None
    auth: AuthConfig | None = None
    ui: UiConfig = Field(default_factory=UiConfig)
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, gt=0, le=100)
    # `[retry]` block in the TOML, applied to every instance that doesn't
    # set its own. Off by default - health checks generally want to
    # observe flakes rather than mask them.
    retry: RetryPolicy | None = None

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
        if self.alerts.webhook is not None:
            configured.add("webhook")
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
    """Return True if any inline credential lives in the TOML.

    Drives the mode-0600 advisory. We deliberately consider *anything* an
    operator would not want world-readable a secret: instance creds,
    every alerter's webhook URL (Slack incoming hooks and generic
    webhooks are both effectively bearer tokens in the URL), any
    webhook auth headers (Authorization / X-API-Key / etc), and the
    server `[auth]` token. Env-var indirections are skipped - they
    don't make the file itself sensitive.
    """
    if any(i.has_inline_secret() for i in cfg.instances.values()):
        return True
    if cfg.alerts is not None:
        if cfg.alerts.slack is not None and cfg.alerts.slack.webhook_url is not None:
            return True
        if cfg.alerts.webhook is not None:
            if cfg.alerts.webhook.url is not None:
                return True
            if cfg.alerts.webhook.headers:
                return True
    if cfg.auth is not None and cfg.auth.token is not None:
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
