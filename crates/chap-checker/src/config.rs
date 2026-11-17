//! Load `chap-checker.toml` into typed, validated config.
//!
//! The schema is the one the Python implementation accepted, field for field, so existing config
//! files keep working. Errors name the field and the problem but never echo a value: a rejected
//! value can be a password, a token or a webhook URL.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use serde::Deserialize;
use serde::de::DeserializeOwned;
use url::Url;

use crate::checks::{self, Status};
use crate::http::{Auth, RetryPolicy, Target, TargetKind};
use crate::runner::TargetEntry;

pub const DEFAULT_CONFIG_FILENAME: &str = "chap-checker.toml";
pub const DEFAULT_CONCURRENCY: usize = 5;
pub const DEFAULT_UI_TITLE: &str = "DHIS2 / Climate Instance Checker";

/// A config that failed to load. Its text is safe to print: it carries no secret values.
#[derive(Debug)]
pub enum ConfigError {
    Io(PathBuf, std::io::Error),
    Toml(String),
    Invalid(Vec<(String, String)>),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Io(path, e) => write!(f, "{}: {e}", path.display()),
            ConfigError::Toml(message) => f.write_str(message),
            ConfigError::Invalid(errors) => {
                write!(f, "{} validation error(s) for CheckerConfig:", errors.len())?;
                for (loc, msg) in errors {
                    let loc = if loc.is_empty() { "<root>" } else { loc };
                    write!(f, "\n  {loc}: {msg}")?;
                }
                Ok(())
            }
        }
    }
}

impl std::error::Error for ConfigError {}

/// Return `value` if set, else the named environment variable, which must be set and non-blank.
fn value_or_env(value: Option<&str>, env_var: Option<&str>, label: &str) -> Result<String, String> {
    if let Some(v) = value {
        return Ok(v.to_owned());
    }
    let Some(var) = env_var else {
        return Err(format!(
            "{label}: neither a literal value nor an env-var name was set."
        ));
    };
    match std::env::var(var) {
        Err(_) => Err(format!(
            "Environment variable '{var}' is not set (referenced by {label})."
        )),
        Ok(v) if v.trim().is_empty() => Err(format!(
            "Environment variable '{var}' is set but empty (referenced by {label})."
        )),
        Ok(v) => Ok(v),
    }
}

/// Check that `token` works as a bearer token: printable ASCII, no spaces, not empty.
pub fn validate_bearer_token(token: &str, label: &str) -> Result<(), String> {
    if token.trim().is_empty() {
        return Err(format!("{label}: bearer token must not be empty."));
    }
    if token != token.trim() {
        return Err(format!(
            "{label}: bearer token must not have leading or trailing whitespace."
        ));
    }
    if !token.bytes().all(|b| (0x21..=0x7e).contains(&b)) {
        return Err(format!(
            "{label}: bearer token may only contain printable ASCII characters without spaces \
             (generate one with `openssl rand -hex 32`)."
        ));
    }
    Ok(())
}

const USERINFO_MESSAGE: &str = "credentials embedded in the URL (https://user:pass@host) are not allowed; \
     they would be echoed in reports, /api/state and alerts.";

/// Parse an `http` or `https` URL without credentials in it. Messages never include the URL.
fn http_url(text: &str, userinfo_hint: &str) -> Result<Url, String> {
    let url = Url::parse(text).map_err(|e| format!("Input should be a valid URL, {e}"))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("URL scheme should be 'http' or 'https'".to_owned());
    }
    if url.host_str().is_none_or(str::is_empty) {
        return Err("Input should be a valid URL, empty host".to_owned());
    }
    reject_userinfo(&url, userinfo_hint)?;
    Ok(url)
}

fn reject_userinfo(url: &Url, hint: &str) -> Result<(), String> {
    if !url.username().is_empty() || url.password().is_some() {
        return Err(format!("{USERINFO_MESSAGE} {hint}"));
    }
    Ok(())
}

fn userinfo_in(text: &str, hint: &str) -> Result<(), String> {
    match Url::parse(text) {
        Ok(url) => reject_userinfo(&url, hint),
        Err(_) => Ok(()),
    }
}

fn default_timeout() -> f64 {
    10.0
}

fn default_true() -> bool {
    true
}

fn default_notify_on() -> Vec<Status> {
    vec![Status::Fail, Status::Error, Status::Warn]
}

/// One `[instances.<key>]` table of `kind = "dhis2"` (the default).
///
/// Auth is `username` with `password` or `password_env` (Basic), or `token` or `token_env` (a
/// Personal Access Token, sent as `Authorization: ApiToken <value>`). Exactly one mode.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Dhis2InstanceConfig {
    #[serde(default, rename = "kind")]
    _kind: Option<String>,
    pub name: Option<String>,
    pub url: String,
    #[serde(default = "default_timeout")]
    pub timeout_s: f64,
    #[serde(default = "default_true")]
    pub verify_tls: bool,
    pub checks: Option<Vec<String>>,
    #[serde(default)]
    pub alerts: Vec<String>,
    pub username: Option<String>,
    pub password: Option<String>,
    pub password_env: Option<String>,
    pub token: Option<String>,
    pub token_env: Option<String>,
    pub retry_policy: Option<RetryPolicy>,
}

/// One `[instances.<key>]` table of `kind = "ogs"`: an Open Geo Service deployment. No auth.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OgsInstanceConfig {
    #[serde(rename = "kind")]
    _kind: String,
    pub name: Option<String>,
    pub url: String,
    #[serde(default = "default_timeout")]
    pub timeout_s: f64,
    #[serde(default = "default_true")]
    pub verify_tls: bool,
    pub checks: Option<Vec<String>>,
    #[serde(default)]
    pub alerts: Vec<String>,
}

/// Any `[instances.<key>]` table.
#[derive(Debug, Clone)]
pub struct InstanceConfig {
    pub kind: TargetKind,
    pub name: Option<String>,
    pub url: Url,
    pub timeout_s: f64,
    pub verify_tls: bool,
    pub checks: Option<Vec<String>>,
    pub alerts: Vec<String>,
    /// DHIS2 only.
    pub dhis2: Option<Dhis2InstanceConfig>,
}

impl InstanceConfig {
    /// True when a password or token is written inline in the file.
    pub fn has_inline_secret(&self) -> bool {
        self.dhis2
            .as_ref()
            .is_some_and(|d| d.password.is_some() || d.token.is_some())
    }

    /// Build the runtime target, resolving `*_env` credentials.
    ///
    /// `default_retry` is the top-level `[retry]` table; a per-instance `retry_policy` wins.
    pub fn to_target(&self, default_retry: Option<&RetryPolicy>) -> Result<Target, String> {
        let (auth, retry) = match &self.dhis2 {
            None => (None, None),
            Some(d) => {
                let auth = if d.token.is_some() || d.token_env.is_some() {
                    Auth::Token(value_or_env(
                        d.token.as_deref(),
                        d.token_env.as_deref(),
                        "token_env",
                    )?)
                } else {
                    Auth::Basic {
                        username: d.username.clone().unwrap_or_default(),
                        password: value_or_env(
                            d.password.as_deref(),
                            d.password_env.as_deref(),
                            "password_env",
                        )?,
                    }
                };
                (
                    Some(auth),
                    d.retry_policy.clone().or_else(|| default_retry.cloned()),
                )
            }
        };
        Ok(Target {
            kind: self.kind,
            base_url: self.url.clone(),
            timeout_s: self.timeout_s,
            verify_tls: self.verify_tls,
            auth,
            retry,
        })
    }

    pub fn to_target_entry(
        &self,
        name: &str,
        default_retry: Option<&RetryPolicy>,
    ) -> Result<TargetEntry, String> {
        Ok(TargetEntry {
            name: name.to_owned(),
            display_name: self.name.clone(),
            target: self.to_target(default_retry)?,
            check_names: self.checks.clone(),
            alerts: self.alerts.clone(),
        })
    }
}

const SLACK_USERINFO_HINT: &str = "Slack incoming-webhook URLs never need them.";
const WEBHOOK_USERINFO_HINT: &str = "Send credentials with the 'headers' table (e.g. Authorization); \
     secrets in the URL path or query string are still accepted.";

/// `[alerts.slack]`: a Slack Incoming Webhook.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SlackAlertConfig {
    pub webhook_url: Option<String>,
    pub webhook_url_env: Option<String>,
    #[serde(default = "default_notify_on")]
    pub notify_on: Vec<Status>,
    #[serde(default = "default_timeout")]
    pub timeout_s: f64,
}

impl SlackAlertConfig {
    pub fn resolve_webhook_url(&self) -> Result<String, String> {
        let value = value_or_env(
            self.webhook_url.as_deref(),
            self.webhook_url_env.as_deref(),
            "alerts.slack.webhook_url_env",
        )?;
        userinfo_in(&value, SLACK_USERINFO_HINT)
            .map_err(|e| format!("alerts.slack.webhook_url_env: {e}"))?;
        Ok(value)
    }
}

/// `[alerts.webhook]`: POST the canonical JSON envelope to any URL. Auth goes in `headers`.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WebhookAlertConfig {
    pub url: Option<String>,
    pub url_env: Option<String>,
    #[serde(default = "default_notify_on")]
    pub notify_on: Vec<Status>,
    #[serde(default = "default_timeout")]
    pub timeout_s: f64,
    #[serde(default)]
    pub headers: BTreeMap<String, String>,
}

impl WebhookAlertConfig {
    pub fn resolve_url(&self) -> Result<String, String> {
        let value = value_or_env(
            self.url.as_deref(),
            self.url_env.as_deref(),
            "alerts.webhook.url_env",
        )?;
        userinfo_in(&value, WEBHOOK_USERINFO_HINT)
            .map_err(|e| format!("alerts.webhook.url_env: {e}"))?;
        Ok(value)
    }
}

/// `[alerts.*]`: one optional table per alerter.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AlertsConfig {
    pub slack: Option<SlackAlertConfig>,
    pub webhook: Option<WebhookAlertConfig>,
}

impl AlertsConfig {
    /// Names of the alerters that have a table.
    pub fn configured(&self) -> BTreeSet<&'static str> {
        let mut names = BTreeSet::new();
        if self.slack.is_some() {
            names.insert("slack");
        }
        if self.webhook.is_some() {
            names.insert("webhook");
        }
        names
    }
}

/// `[auth]`: requires `Authorization: Bearer <token>` on `chap-checker serve`'s `/api/*` routes.
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthConfig {
    pub token: Option<String>,
    pub token_env: Option<String>,
}

impl std::fmt::Debug for AuthConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AuthConfig")
            .field("token", &self.token.as_ref().map(|_| "<redacted>"))
            .field("token_env", &self.token_env)
            .finish()
    }
}

impl AuthConfig {
    /// The bearer token, read from the environment when `token_env` is set.
    pub fn resolve_token(&self) -> Result<String, String> {
        let value = value_or_env(
            self.token.as_deref(),
            self.token_env.as_deref(),
            "auth.token_env",
        )?;
        if self.token.is_none() {
            let label = format!(
                "auth.token_env ({})",
                self.token_env.as_deref().unwrap_or("")
            );
            validate_bearer_token(&value, &label)?;
        }
        Ok(value)
    }
}

/// The `[ui] theme` values.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum UiTheme {
    #[default]
    Phosphor,
    Amber,
    High,
    Tokyo,
    Dhis2,
}

impl UiTheme {
    pub fn as_str(self) -> &'static str {
        match self {
            UiTheme::Phosphor => "phosphor",
            UiTheme::Amber => "amber",
            UiTheme::High => "high",
            UiTheme::Tokyo => "tokyo",
            UiTheme::Dhis2 => "dhis2",
        }
    }
}

/// `[ui]`: the title and starting theme of the TUI and the browser dashboard.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UiConfig {
    #[serde(default = "default_title")]
    pub title: String,
    #[serde(default)]
    pub theme: UiTheme,
}

fn default_title() -> String {
    DEFAULT_UI_TITLE.to_owned()
}

impl Default for UiConfig {
    fn default() -> Self {
        Self {
            title: default_title(),
            theme: UiTheme::default(),
        }
    }
}

/// The whole `chap-checker.toml`.
#[derive(Debug, Clone)]
pub struct CheckerConfig {
    pub instances: IndexMap<String, InstanceConfig>,
    pub alerts: Option<AlertsConfig>,
    pub auth: Option<AuthConfig>,
    pub ui: UiConfig,
    pub concurrency: usize,
    /// `[retry]`, applied to every DHIS2 instance without its own `retry_policy`.
    pub retry: Option<RetryPolicy>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    #[serde(default)]
    instances: IndexMap<String, toml::Value>,
    alerts: Option<AlertsConfig>,
    auth: Option<AuthConfig>,
    #[serde(default)]
    ui: UiConfig,
    concurrency: Option<i64>,
    retry: Option<RetryPolicy>,
}

impl CheckerConfig {
    /// One instance by name, or the error message listing what exists.
    pub fn get(&self, name: &str) -> Result<&InstanceConfig, String> {
        self.instances.get(name).ok_or_else(|| {
            let mut names: Vec<&str> = self.instances.keys().map(String::as_str).collect();
            names.sort_unstable();
            let available = if names.is_empty() {
                "<none>".to_owned()
            } else {
                names.join(", ")
            };
            format!("Unknown instance '{name}'. Available: {available}")
        })
    }

    /// Build a target entry for every instance, resolving `*_env` credentials.
    pub fn target_entries(&self) -> Result<Vec<TargetEntry>, String> {
        self.instances
            .iter()
            .map(|(name, inst)| inst.to_target_entry(name, self.retry.as_ref()))
            .collect()
    }

    /// Parse and validate TOML text.
    pub fn parse(text: &str) -> Result<Self, ConfigError> {
        let value: toml::Value =
            toml::from_str(text).map_err(|e| ConfigError::Toml(toml_message(&e, text)))?;
        let raw: RawConfig = typed(value, "").map_err(|e| ConfigError::Invalid(vec![e]))?;
        let mut errors: Vec<(String, String)> = Vec::new();

        let mut instances = IndexMap::new();
        for (key, value) in raw.instances {
            let loc = format!("instances.{key}");
            match instance(value, &loc) {
                Ok(inst) => {
                    instances.insert(key, inst);
                }
                Err(mut errs) => errors.append(&mut errs),
            }
        }

        if let Some(alerts) = &raw.alerts {
            if let Some(slack) = &alerts.slack {
                let loc = "alerts.slack";
                if slack.webhook_url.is_some() == slack.webhook_url_env.is_some() {
                    errors.push((
                        loc.into(),
                        "set exactly one of 'webhook_url' or 'webhook_url_env'".into(),
                    ));
                }
                if let Some(url) = &slack.webhook_url
                    && let Err(e) = http_url(url, SLACK_USERINFO_HINT)
                {
                    errors.push((format!("{loc}.webhook_url"), e));
                }
                if slack.timeout_s <= 0.0 {
                    errors.push((
                        format!("{loc}.timeout_s"),
                        "Input should be greater than 0".into(),
                    ));
                }
            }
            if let Some(webhook) = &alerts.webhook {
                let loc = "alerts.webhook";
                if webhook.url.is_some() == webhook.url_env.is_some() {
                    errors.push((loc.into(), "set exactly one of 'url' or 'url_env'".into()));
                }
                if let Some(url) = &webhook.url
                    && let Err(e) = http_url(url, WEBHOOK_USERINFO_HINT)
                {
                    errors.push((format!("{loc}.url"), e));
                }
                if webhook.timeout_s <= 0.0 {
                    errors.push((
                        format!("{loc}.timeout_s"),
                        "Input should be greater than 0".into(),
                    ));
                }
            }
        }

        if let Some(auth) = &raw.auth {
            if let Some(token) = &auth.token
                && let Err(e) = validate_bearer_token(token, "auth.token")
            {
                errors.push(("auth.token".into(), e));
            }
            if auth.token.is_some() == auth.token_env.is_some() {
                errors.push((
                    "auth".into(),
                    "set exactly one of 'token' or 'token_env' in [auth] (or omit the [auth] block entirely to disable auth)."
                        .into(),
                ));
            }
        }

        let title_len = raw.ui.title.chars().count();
        if !(1..=120).contains(&title_len) {
            errors.push((
                "ui.title".into(),
                "must be between 1 and 120 characters".into(),
            ));
        }

        let concurrency = raw.concurrency.unwrap_or(DEFAULT_CONCURRENCY as i64);
        if !(1..=100).contains(&concurrency) {
            errors.push(("concurrency".into(), "must be between 1 and 100".into()));
        }

        if let Some(retry) = &raw.retry {
            for (field, problem) in retry.problems() {
                errors.push((format!("retry.{field}"), problem));
            }
        }

        let configured = raw
            .alerts
            .as_ref()
            .map(AlertsConfig::configured)
            .unwrap_or_default();
        for (name, inst) in &instances {
            let unknown: Vec<&str> = inst
                .alerts
                .iter()
                .map(String::as_str)
                .filter(|a| !configured.contains(a))
                .collect();
            if !unknown.is_empty() {
                let have = if configured.is_empty() {
                    "<none>".to_owned()
                } else {
                    configured.iter().copied().collect::<Vec<_>>().join(", ")
                };
                errors.push((
                    String::new(),
                    format!(
                        "instance '{name}' references unconfigured alerter(s): {}. Configured alerters: {have}",
                        unknown.join(", ")
                    ),
                ));
            }
        }

        if !errors.is_empty() {
            return Err(ConfigError::Invalid(errors));
        }
        Ok(Self {
            instances,
            alerts: raw.alerts,
            auth: raw.auth,
            ui: raw.ui,
            concurrency: concurrency as usize,
            retry: raw.retry,
        })
    }

    /// True when any credential is written inline in the file (drives the mode-0600 advisory).
    ///
    /// Instance credentials, any alerter's literal URL (webhook URLs are bearer tokens), webhook
    /// headers and the `[auth]` token all count. Env-var indirections don't.
    pub fn has_inline_secret(&self) -> bool {
        if self
            .instances
            .values()
            .any(InstanceConfig::has_inline_secret)
        {
            return true;
        }
        if let Some(alerts) = &self.alerts {
            if alerts
                .slack
                .as_ref()
                .is_some_and(|s| s.webhook_url.is_some())
            {
                return true;
            }
            if let Some(w) = &alerts.webhook
                && (w.url.is_some() || !w.headers.is_empty())
            {
                return true;
            }
        }
        self.auth.as_ref().is_some_and(|a| a.token.is_some())
    }
}

/// Validate one `[instances.<key>]` table.
fn instance(value: toml::Value, loc: &str) -> Result<InstanceConfig, Vec<(String, String)>> {
    let kind = match value.get("kind") {
        None => TargetKind::Dhis2,
        Some(toml::Value::String(k)) if k == "dhis2" => TargetKind::Dhis2,
        Some(toml::Value::String(k)) if k == "ogs" => TargetKind::Ogs,
        Some(_) => {
            return Err(vec![(
                format!("{loc}.kind"),
                "Input tag does not match any of the expected tags: 'dhis2', 'ogs'".into(),
            )]);
        }
    };
    let mut errors = Vec::new();
    let (name, url, timeout_s, verify_tls, check_names, alerts, dhis2) = match kind {
        TargetKind::Dhis2 => {
            let d: Dhis2InstanceConfig = typed(value, loc).map_err(|e| vec![e])?;
            for (field, literal) in [("password", &d.password), ("token", &d.token)] {
                if literal.as_deref().is_some_and(|v| v.trim().is_empty()) {
                    errors.push((
                        format!("{loc}.{field}"),
                        "must not be empty; remove the field or set a real value.".into(),
                    ));
                }
            }
            if let Err(e) = auth_mode(&d) {
                errors.push((loc.to_owned(), e));
            }
            if let Some(retry) = &d.retry_policy {
                for (field, problem) in retry.problems() {
                    errors.push((format!("{loc}.retry_policy.{field}"), problem));
                }
            }
            (
                d.name.clone(),
                d.url.clone(),
                d.timeout_s,
                d.verify_tls,
                d.checks.clone(),
                d.alerts.clone(),
                Some(d),
            )
        }
        TargetKind::Ogs => {
            let o: OgsInstanceConfig = typed(value, loc).map_err(|e| vec![e])?;
            (
                o.name,
                o.url,
                o.timeout_s,
                o.verify_tls,
                o.checks,
                o.alerts,
                None,
            )
        }
    };
    let url = match http_url(
        &url,
        "Use the 'username' + 'password' / 'password_env' or 'token' / 'token_env' fields instead.",
    ) {
        Ok(u) => Some(u),
        Err(e) => {
            errors.push((format!("{loc}.url"), e));
            None
        }
    };
    if timeout_s <= 0.0 {
        errors.push((
            format!("{loc}.timeout_s"),
            "Input should be greater than 0".into(),
        ));
    }
    if let Some(names) = &check_names {
        if names.is_empty() {
            errors.push((
                format!("{loc}.checks"),
                "List should have at least 1 item after validation".into(),
            ));
        } else if let Err(e) = checks_for_kind(names, kind) {
            errors.push((loc.to_owned(), e));
        }
    }
    match (errors.is_empty(), url) {
        (true, Some(url)) => Ok(InstanceConfig {
            kind,
            name,
            url,
            timeout_s,
            verify_tls,
            checks: check_names,
            alerts,
            dhis2,
        }),
        _ => Err(errors),
    }
}

fn auth_mode(d: &Dhis2InstanceConfig) -> Result<(), String> {
    let has_password = d.password.is_some() || d.password_env.is_some();
    let has_token = d.token.is_some() || d.token_env.is_some();
    if has_password && has_token {
        return Err("set either a password (password / password_env) or a token (token / token_env), not both.".into());
    }
    if !has_password && !has_token {
        return Err(
            "set exactly one of 'password', 'password_env', 'token', or 'token_env'.".into(),
        );
    }
    if has_password {
        if d.password.is_some() && d.password_env.is_some() {
            return Err("set exactly one of 'password' or 'password_env'".into());
        }
        if d.username.is_none() {
            return Err("password / password_env requires 'username'.".into());
        }
    } else if d.token.is_some() && d.token_env.is_some() {
        return Err("set exactly one of 'token' or 'token_env'".into());
    }
    Ok(())
}

/// Every name must be a registered check that applies to `kind`.
fn checks_for_kind(names: &[String], kind: TargetKind) -> Result<(), String> {
    let unknown: Vec<&str> = names
        .iter()
        .map(String::as_str)
        .filter(|n| checks::find(n).is_none())
        .collect();
    if !unknown.is_empty() {
        return Err(format!(
            "unknown check name(s): {}. Known: {}",
            unknown.join(", "),
            checks::known_names()
        ));
    }
    let wrong: Vec<&str> = names
        .iter()
        .map(String::as_str)
        .filter(|n| checks::find(n).is_some_and(|c| !c.applies_to(kind)))
        .collect();
    if !wrong.is_empty() {
        let mut applicable: Vec<&str> = checks::all_checks()
            .into_iter()
            .filter(|c| c.applies_to(kind))
            .map(|c| c.name)
            .collect();
        applicable.sort_unstable();
        return Err(format!(
            "check(s) {} do not apply to kind '{}'. Checks for '{}': {}",
            wrong.join(", "),
            kind.as_str(),
            kind.as_str(),
            applicable.join(", ")
        ));
    }
    Ok(())
}

/// Deserialize `value` into `T`, reporting the failing field's dotted path under `prefix`.
fn typed<T: DeserializeOwned>(value: toml::Value, prefix: &str) -> Result<T, (String, String)> {
    serde_path_to_error::deserialize(value).map_err(|e| {
        let path = e.path().to_string();
        let path = if path == "." { String::new() } else { path };
        let loc = match (prefix.is_empty(), path.is_empty()) {
            (true, _) => path,
            (false, true) => prefix.to_owned(),
            (false, false) => format!("{prefix}.{path}"),
        };
        // toml appends "\nin `field`" lines; the path already says where.
        let message = e.into_inner().to_string();
        (
            loc,
            redact(message.lines().next().unwrap_or_default().trim()),
        )
    })
}

/// Drop the offending value from serde's "invalid type/value" messages; keep the rest.
fn redact(message: &str) -> String {
    let pattern = regex::Regex::new(
        r#"^(invalid (?:type|value): [a-z ]+?)(?: `[^`]*`| "(?:[^"\\]|\\.)*")?(, expected .*)$"#,
    )
    .expect("valid regex");
    match pattern.captures(message) {
        Some(c) => format!("{}{}", &c[1], &c[2]),
        None => message.to_owned(),
    }
}

/// A TOML syntax error as `message (at line L, column C)`, without the source line it quotes.
fn toml_message(err: &toml::de::Error, text: &str) -> String {
    let message = err.message().trim();
    match err.span() {
        Some(span) => {
            let before = &text[..span.start.min(text.len())];
            let line = before.matches('\n').count() + 1;
            let column = before.rsplit('\n').next().map_or(0, |l| l.chars().count()) + 1;
            format!("{message} (at line {line}, column {column})")
        }
        None => message.to_owned(),
    }
}

/// `./chap-checker.toml`.
pub fn default_config_path() -> PathBuf {
    PathBuf::from(DEFAULT_CONFIG_FILENAME)
}

/// Read and validate a config file, warning when inline secrets sit in a group- or
/// world-readable file.
pub fn load_config(path: &Path) -> Result<CheckerConfig, ConfigError> {
    let text = std::fs::read_to_string(path).map_err(|e| ConfigError::Io(path.to_path_buf(), e))?;
    let cfg = CheckerConfig::parse(&text)?;
    warn_if_insecure(path, &cfg);
    Ok(cfg)
}

#[cfg(unix)]
fn warn_if_insecure(path: &Path, cfg: &CheckerConfig) {
    use std::os::unix::fs::PermissionsExt;
    if !cfg.has_inline_secret() {
        return;
    }
    let Ok(meta) = std::fs::metadata(path) else {
        return;
    };
    let mode = meta.permissions().mode() & 0o777;
    if mode & 0o077 != 0 {
        tracing::warn!(
            "{} contains inline credentials and is mode {mode:o}; recommend `chmod 600 {}`.",
            path.display(),
            path.display()
        );
    }
}

#[cfg(not(unix))]
fn warn_if_insecure(_path: &Path, _cfg: &CheckerConfig) {}

#[cfg(test)]
mod tests {
    use super::*;

    fn errors(text: &str) -> String {
        CheckerConfig::parse(text).unwrap_err().to_string()
    }

    #[test]
    fn parses_the_init_template_shape() {
        let cfg = CheckerConfig::parse(
            r#"
            concurrency = 3
            [instances.play]
            name = "Play"
            url = "https://play.im.dhis2.org/dev"
            username = "admin"
            password = "district"
            checks = ["dhis2_ping", "dhis2_system_info"]
            alerts = ["slack"]

            [instances.nepal]
            kind = "ogs"
            url = "https://ogs.example.org"

            [alerts.slack]
            webhook_url_env = "SLACK"
            notify_on = ["fail"]

            [retry]
            max_attempts = 2
            "#,
        )
        .unwrap();
        assert_eq!(cfg.instances.keys().collect::<Vec<_>>(), ["play", "nepal"]);
        assert_eq!(cfg.instances["nepal"].kind, TargetKind::Ogs);
        assert_eq!(cfg.concurrency, 3);
        let entries = cfg.target_entries().unwrap();
        assert_eq!(entries[0].target.retry.as_ref().unwrap().max_attempts, 2);
        assert!(entries[1].target.retry.is_none());
        assert!(cfg.has_inline_secret());
    }

    #[test]
    fn auth_modes() {
        let both = errors(
            r#"[instances.a]
            url = "https://x"
            username = "u"
            password = "p"
            token = "t""#,
        );
        assert!(
            both.contains("instances.a: set either a password"),
            "{both}"
        );
        let none = errors("[instances.a]\nurl = \"https://x\"");
        assert!(none.contains("set exactly one of 'password'"), "{none}");
    }

    #[test]
    fn rejects_credentials_in_urls_without_echoing_them() {
        let text = errors(
            r#"[instances.a]
            url = "https://user:hunter2@x"
            token = "t""#,
        );
        assert!(
            text.contains("instances.a.url: credentials embedded"),
            "{text}"
        );
        assert!(!text.contains("hunter2"));
    }

    #[test]
    fn type_errors_do_not_echo_values() {
        let text = errors(
            r#"[instances.a]
            url = "https://x"
            username = "u"
            password = 123456"#,
        );
        assert!(
            text.contains("instances.a.password: invalid type: integer, expected a string"),
            "{text}"
        );
        assert!(!text.contains("123456"));
    }

    #[test]
    fn unknown_fields_checks_and_alerters() {
        assert!(errors("bogus = 1").contains("unknown field `bogus`"));
        let text = errors(
            r#"[instances.a]
            kind = "ogs"
            url = "https://x"
            checks = ["dhis2_ping"]
            alerts = ["slack"]"#,
        );
        assert!(text.contains("do not apply to kind 'ogs'"), "{text}");
        let text = errors(
            r#"[instances.a]
            kind = "ogs"
            url = "https://x"
            alerts = ["slack"]"#,
        );
        assert!(
            text.contains("unconfigured alerter(s): slack. Configured alerters: <none>"),
            "{text}"
        );
        assert!(
            errors("[instances.a]\nkind = \"ogs\"\nurl = \"https://x\"\npassword = \"p\"")
                .contains("unknown field")
        );
    }

    #[test]
    fn syntax_errors_name_the_line() {
        let text = errors("[instances.a]\nurl = \"https://x\nx = 1\n");
        assert!(text.contains("line 2"), "{text}");
    }

    #[test]
    fn env_secrets_resolve_and_reject_blank() {
        // SAFETY: tests in this module use unique variable names.
        unsafe {
            std::env::set_var("CHAP_TEST_TOKEN_BLANK", "  ");
        }
        let cfg = CheckerConfig::parse(
            "[instances.a]\nurl = \"https://x\"\ntoken_env = \"CHAP_TEST_TOKEN_BLANK\"",
        )
        .unwrap();
        let err = cfg.target_entries().unwrap_err();
        assert_eq!(
            err,
            "Environment variable 'CHAP_TEST_TOKEN_BLANK' is set but empty (referenced by token_env)."
        );
        assert!(cfg.instances["a"].url.as_str() == "https://x/");
    }

    #[test]
    fn bearer_tokens() {
        assert!(validate_bearer_token("abc123", "t").is_ok());
        assert!(validate_bearer_token(" abc", "t").is_err());
        assert!(validate_bearer_token("a b", "t").is_err());
    }
}
