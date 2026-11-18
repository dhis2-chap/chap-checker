//! Targets (the servers under test) and the HTTP client checks talk to them through.
//!
//! A DHIS2 target authenticates with Basic auth or a Personal Access Token (sent as
//! `Authorization: ApiToken <value>`), never follows redirects (a redirect from the API is a fact a
//! check reports, not something to chase with credentials attached), and can retry transient
//! failures per a [`RetryPolicy`]. Every other kind has no auth and follows up to five redirects.

use std::time::Duration;

use rand::RngExt;
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use url::Url;

/// What kind of server a target is: one per plugin ([`crate::plugins`]). It picks which checks
/// apply, which config fields an instance takes, and how its tile is labelled.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TargetKind {
    Dhis2,
    Ogs,
    Chap,
    Caps,
    Http,
}

impl TargetKind {
    pub const ALL: [TargetKind; 5] = [
        TargetKind::Dhis2,
        TargetKind::Ogs,
        TargetKind::Chap,
        TargetKind::Caps,
        TargetKind::Http,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            TargetKind::Dhis2 => "dhis2",
            TargetKind::Ogs => "ogs",
            TargetKind::Chap => "chap",
            TargetKind::Caps => "caps",
            TargetKind::Http => "http",
        }
    }

    pub fn parse(text: &str) -> Option<TargetKind> {
        TargetKind::ALL.into_iter().find(|k| k.as_str() == text)
    }

    /// Human-readable platform label, shown on dashboard tiles.
    pub fn platform_label(self) -> &'static str {
        crate::plugins::plugin(self).label
    }
}

/// How a DHIS2 target authenticates.
#[derive(Clone)]
pub enum Auth {
    Basic { username: String, password: String },
    Token(String),
}

impl std::fmt::Debug for Auth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Auth::Basic { username, .. } => write!(f, "Basic({username}, <redacted>)"),
            Auth::Token(_) => write!(f, "Token(<redacted>)"),
        }
    }
}

/// Retries for transient failures: transport errors and the configured statuses.
///
/// Off by default: a health checker usually wants to see flakes, not mask them. Field names and
/// defaults match the `[retry]` table the Python implementation accepted.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(default)]
pub struct RetryPolicy {
    /// Total attempt count including the first call.
    pub max_attempts: u32,
    /// Initial backoff in seconds before the second attempt.
    pub base_delay: f64,
    /// Hard cap on each sleep, in seconds.
    pub max_delay: f64,
    /// Multiplier applied per attempt.
    pub backoff_factor: f64,
    /// Fractional random jitter applied to each delay (0.1 = +/- 10%).
    pub jitter: f64,
    /// Statuses that trigger a retry.
    pub retry_statuses: Vec<u16>,
    /// Accepted for compatibility; checks only send GET requests.
    pub retry_non_idempotent: bool,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            base_delay: 0.5,
            max_delay: 30.0,
            backoff_factor: 2.0,
            jitter: 0.1,
            retry_statuses: vec![429, 502, 503, 504],
            retry_non_idempotent: false,
        }
    }
}

impl RetryPolicy {
    /// Return the problems with this policy, as `(field, message)` pairs.
    pub fn problems(&self) -> Vec<(&'static str, String)> {
        let ge = |n: &str| format!("Input should be greater than or equal to {n}");
        let le = |n: &str| format!("Input should be less than or equal to {n}");
        let mut out = Vec::new();
        if self.max_attempts < 1 {
            out.push(("max_attempts", ge("1")));
        } else if self.max_attempts > 10 {
            out.push(("max_attempts", le("10")));
        }
        if self.base_delay < 0.0 {
            out.push(("base_delay", ge("0")));
        }
        if self.max_delay <= 0.0 {
            out.push(("max_delay", "Input should be greater than 0".to_owned()));
        }
        if self.backoff_factor < 1.0 {
            out.push(("backoff_factor", ge("1")));
        }
        if self.jitter < 0.0 {
            out.push(("jitter", ge("0")));
        } else if self.jitter > 1.0 {
            out.push(("jitter", le("1")));
        }
        out
    }

    /// The sleep before attempt `attempt + 1` (`attempt` is 1-based).
    fn delay(&self, attempt: u32) -> Duration {
        let raw = self.base_delay * self.backoff_factor.powi(attempt.saturating_sub(1) as i32);
        let capped = raw.min(self.max_delay);
        let seconds = if self.jitter > 0.0 {
            let factor = rand::rng().random_range(-self.jitter..=self.jitter);
            capped * (1.0 + factor)
        } else {
            capped
        };
        Duration::from_secs_f64(seconds.max(0.0))
    }
}

/// One server under test.
#[derive(Debug, Clone)]
pub struct Target {
    pub kind: TargetKind,
    pub base_url: Url,
    pub timeout_s: f64,
    pub verify_tls: bool,
    /// DHIS2 only.
    pub auth: Option<Auth>,
    /// DHIS2 only.
    pub retry: Option<RetryPolicy>,
    /// Settings only this kind's plugin reads (the `http` kind's expectations).
    pub params: serde_json::Value,
}

impl Target {
    /// The base URL as reports print it (normalized, so a bare host ends in `/`).
    pub fn url_string(&self) -> String {
        self.base_url.to_string()
    }

    /// Open the client this kind's checks use.
    pub fn open(&self) -> Result<Client, RequestError> {
        let redirects = match self.kind {
            TargetKind::Dhis2 => Policy::none(),
            _ => Policy::limited(5),
        };
        let http = build_http(self.timeout_s, self.verify_tls, redirects)?;
        Ok(Client {
            http,
            base: self.url_string().trim_end_matches('/').to_owned(),
            auth: self.auth.clone(),
            retry: self.retry.clone(),
        })
    }
}

/// Build a reqwest client with per-phase timeouts (connect, and each read), like httpx's.
pub fn build_http(
    timeout_s: f64,
    verify_tls: bool,
    redirects: Policy,
) -> Result<reqwest::Client, RequestError> {
    let timeout = Duration::from_secs_f64(timeout_s);
    reqwest::Client::builder()
        .connect_timeout(connect_timeout(timeout))
        .read_timeout(timeout)
        .tls_danger_accept_invalid_certs(!verify_tls)
        .redirect(redirects)
        .user_agent(concat!("chap-checker/", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|e| RequestError::from_reqwest(&e))
}

/// reqwest starts the read timeout with the request, not once connected, so with equal
/// timeouts a host that never answers trips the read timeout first and reads as a
/// `ReadTimeout`. Ending the connect a little earlier keeps it a `ConnectTimeout`, as httpx
/// reports it.
fn connect_timeout(timeout: Duration) -> Duration {
    timeout.saturating_sub((timeout / 20).min(Duration::from_millis(100)))
}

/// A response with its body read.
#[derive(Debug, Clone)]
pub struct Response {
    pub status: u16,
    pub url: Url,
    pub content_type: Option<String>,
    pub location: Option<String>,
    pub body: Vec<u8>,
}

impl Response {
    /// Parse the body as JSON, whatever the content type says.
    pub fn json(&self) -> Option<serde_json::Value> {
        serde_json::from_slice(&self.body).ok()
    }

    /// The body as text, lossily decoded.
    pub fn text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }

    async fn read(response: reqwest::Response) -> Result<Self, RequestError> {
        let status = response.status().as_u16();
        let url = response.url().clone();
        let header = |name: reqwest::header::HeaderName| {
            response
                .headers()
                .get(name)
                .and_then(|v| v.to_str().ok())
                .map(str::to_owned)
        };
        let content_type = header(reqwest::header::CONTENT_TYPE);
        let location = header(reqwest::header::LOCATION);
        let body = response
            .bytes()
            .await
            .map_err(|e| RequestError::from_reqwest(&e))?
            .to_vec();
        Ok(Self {
            status,
            url,
            content_type,
            location,
            body,
        })
    }
}

/// A transport failure: no response came back.
#[derive(Debug, Clone)]
pub struct RequestError {
    /// The failure's class, named like httpx's exceptions so messages read as before.
    pub kind: &'static str,
    pub message: String,
    retryable: bool,
}

impl RequestError {
    pub fn from_reqwest(err: &reqwest::Error) -> Self {
        let kind = if err.is_timeout() {
            if err.is_connect() {
                "ConnectTimeout"
            } else {
                "ReadTimeout"
            }
        } else if err.is_connect() {
            "ConnectError"
        } else if err.is_redirect() {
            "TooManyRedirects"
        } else if err.is_body() || err.is_decode() {
            "ReadError"
        } else if err.is_builder() {
            "InvalidURL"
        } else {
            "RequestError"
        };
        Self {
            kind,
            message: error_chain(err),
            retryable: err.is_timeout() || err.is_connect() || err.is_request(),
        }
    }

    /// Render as `Kind (path): message`, with `(no message)` when there is none.
    pub fn describe(&self, path: Option<&str>) -> String {
        let base = if self.message.trim().is_empty() {
            "(no message)"
        } else {
            self.message.trim()
        };
        match path {
            Some(p) => format!("{} ({}): {}", self.kind, p, base),
            None => format!("{}: {}", self.kind, base),
        }
    }
}

impl std::fmt::Display for RequestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.describe(None))
    }
}

impl std::error::Error for RequestError {}

/// The error and its sources, innermost last, without the URL reqwest prefixes.
fn error_chain(err: &reqwest::Error) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut source = std::error::Error::source(err);
    while let Some(s) = source {
        let text = s.to_string();
        if !parts.iter().any(|p| p.contains(&text)) {
            parts.push(text);
        }
        source = s.source();
    }
    if parts.is_empty() {
        let mut text = err.to_string();
        if let Some(url) = err.url() {
            text = text
                .replace(&format!(" ({url})"), "")
                .replace(&format!(" for url ({url})"), "");
        }
        return text;
    }
    parts.join(": ")
}

/// The client a check gets for its target.
#[derive(Debug, Clone)]
pub struct Client {
    http: reqwest::Client,
    base: String,
    auth: Option<Auth>,
    retry: Option<RetryPolicy>,
}

impl Client {
    /// GET `path` (joined onto the target's base URL) without raising on 4xx/5xx.
    pub async fn get(&self, path: &str) -> Result<Response, RequestError> {
        self.get_with(path, &[], &[]).await
    }

    /// GET with query parameters and extra headers.
    pub async fn get_with(
        &self,
        path: &str,
        params: &[(&str, &str)],
        headers: &[(&str, &str)],
    ) -> Result<Response, RequestError> {
        let url = format!("{}{}", self.base, path);
        let attempts = self.retry.as_ref().map_or(1, |r| r.max_attempts.max(1));
        let mut attempt = 1;
        loop {
            let mut request = self.http.get(&url).query(params);
            for (name, value) in headers {
                request = request.header(*name, *value);
            }
            request = match &self.auth {
                Some(Auth::Basic { username, password }) => {
                    request.basic_auth(username, Some(password))
                }
                Some(Auth::Token(token)) => {
                    request.header("Authorization", format!("ApiToken {token}"))
                }
                None => request,
            };
            let outcome = match request.send().await {
                Ok(response) => {
                    let retry_after = response
                        .headers()
                        .get(reqwest::header::RETRY_AFTER)
                        .and_then(|v| v.to_str().ok())
                        .and_then(|v| v.trim().parse::<f64>().ok())
                        .map(|s| s.max(0.0));
                    Response::read(response).await.map(|r| (r, retry_after))
                }
                Err(e) => Err(RequestError::from_reqwest(&e)),
            };
            let Some(policy) = self.retry.as_ref().filter(|_| attempt < attempts) else {
                return outcome.map(|(r, _)| r);
            };
            let delay = match &outcome {
                Ok((response, retry_after)) if policy.retry_statuses.contains(&response.status) => {
                    match retry_after {
                        Some(hint) => Duration::from_secs_f64(hint.min(policy.max_delay)),
                        None => policy.delay(attempt),
                    }
                }
                Err(e) if e.retryable => policy.delay(attempt),
                _ => return outcome.map(|(r, _)| r),
            };
            tracing::debug!(
                "retrying GET {path} (attempt {}) after {:?}",
                attempt + 1,
                delay
            );
            tokio::time::sleep(delay).await;
            attempt += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connect_ends_before_the_read_timeout() {
        let ms = |n| Duration::from_millis(n);
        assert_eq!(connect_timeout(ms(10_000)), ms(9_900));
        assert_eq!(connect_timeout(ms(1_000)), ms(950));
        assert_eq!(connect_timeout(Duration::ZERO), Duration::ZERO);
    }

    #[test]
    fn delay_backs_off_and_caps() {
        let policy = RetryPolicy {
            jitter: 0.0,
            max_delay: 1.5,
            ..RetryPolicy::default()
        };
        assert_eq!(policy.delay(1), Duration::from_secs_f64(0.5));
        assert_eq!(policy.delay(2), Duration::from_secs_f64(1.0));
        assert_eq!(policy.delay(3), Duration::from_secs_f64(1.5));
    }

    #[test]
    fn describe_falls_back_to_no_message() {
        let err = RequestError {
            kind: "ReadTimeout",
            message: String::new(),
            retryable: true,
        };
        assert_eq!(
            err.describe(Some("/api/me")),
            "ReadTimeout (/api/me): (no message)"
        );
    }
}
