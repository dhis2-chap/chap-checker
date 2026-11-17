//! Alerters: deliver transitions to Slack or to any URL that takes a JSON POST.
//!
//! Both alerters share one transport: POST, a timeout, no redirects (a POST body can't be safely
//! replayed across one, so a 3xx means the receiver never got the alert) and an error on anything
//! but 2xx. The dispatcher catches the error and skips saving state, so the transitions are sent
//! again on the next run. Only the payload differs.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Duration;

use chrono::Utc;
use reqwest::redirect::Policy;
use serde::Serialize;
use serde_json::{Value, json};

use crate::checks::Status;
use crate::config::AlertsConfig;
use crate::runner::{RunReport, TargetEntry};
use crate::state_store::{
    ReceiverSpec, StateLock, Transition, TransitionKind, compute_receiver_transitions, load_state,
    save_state,
};

/// A registered alerter type, for `chap-checker alerts list`.
pub struct AlerterInfo {
    pub name: &'static str,
    pub description: &'static str,
    pub toml_example: &'static str,
}

pub const ALERTER_TYPES: [AlerterInfo; 2] = [
    AlerterInfo {
        name: "slack",
        description: "Slack Incoming Webhook (Block Kit message with colored attachments).",
        toml_example: r#"[alerts.slack]
# webhook_url = "https://hooks.slack.com/services/REPLACE/ME/HERE"  # Inline URL (NOT recommended for shared configs)
webhook_url_env = "SLACK_WEBHOOK_URL"            # Env var holding the URL (recommended)
notify_on = ["fail", "error", "warn"]            # Statuses that fire alerts (any of: ok, warn, fail, error)
timeout_s = 10.0                                  # HTTP timeout in seconds (must be > 0)
"#,
    },
    AlerterInfo {
        name: "webhook",
        description: "Generic HTTP POST with a canonical JSON envelope (any URL that accepts application/json).",
        toml_example: r#"[alerts.webhook]
url = "https://example.com/hook"            # POST destination (exactly one of url / url_env)
# url_env = "MY_WEBHOOK_URL"                # Env var name holding the URL (recommended for shared configs)
notify_on = ["fail", "error", "warn"]       # Statuses that fire alerts (any of: ok, warn, fail, error)
timeout_s = 10.0                            # HTTP timeout in seconds (must be > 0)
headers = { "X-Custom-Header" = "value" }   # Optional literal HTTP headers (auth tokens go here for now)
"#,
    },
];

/// The payload shape an alerter posts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flavor {
    Slack,
    Webhook,
}

/// A configured alerter with the statuses it cares about.
#[derive(Debug, Clone)]
pub struct Alerter {
    pub name: &'static str,
    pub flavor: Flavor,
    url: String,
    timeout_s: f64,
    headers: BTreeMap<String, String>,
    pub notify_on: BTreeSet<Status>,
}

impl Alerter {
    pub fn slack(url: String, timeout_s: f64, notify_on: BTreeSet<Status>) -> Self {
        Self {
            name: "slack",
            flavor: Flavor::Slack,
            url,
            timeout_s,
            headers: BTreeMap::new(),
            notify_on,
        }
    }

    pub fn webhook(
        url: String,
        timeout_s: f64,
        headers: BTreeMap<String, String>,
        notify_on: BTreeSet<Status>,
    ) -> Self {
        Self {
            name: "webhook",
            flavor: Flavor::Webhook,
            url,
            timeout_s,
            headers,
            notify_on,
        }
    }

    /// The JSON body this alerter posts for `transitions`.
    pub fn payload(&self, transitions: &[Transition]) -> Value {
        match self.flavor {
            Flavor::Slack => slack_payload(transitions),
            Flavor::Webhook => webhook_payload(transitions),
        }
    }

    /// POST the transitions. A no-op for an empty list.
    pub async fn notify(&self, transitions: &[Transition]) -> Result<(), String> {
        if transitions.is_empty() {
            return Ok(());
        }
        let http = reqwest::Client::builder()
            .timeout(crate::http::seconds(self.timeout_s))
            .redirect(Policy::none())
            .user_agent(concat!("chap-checker/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|e| e.to_string())?;
        let mut request = http.post(&self.url).json(&self.payload(transitions));
        for (name, value) in &self.headers {
            request = request.header(name, value);
        }
        let response = request
            .send()
            .await
            .map_err(|e| crate::http::RequestError::from_reqwest(&e).describe(None))?;
        let status = response.status().as_u16();
        if (200..300).contains(&status) {
            return Ok(());
        }
        let detail = if (300..400).contains(&status) {
            let location = response
                .headers()
                .get(reqwest::header::LOCATION)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("<none>")
                .to_owned();
            format!(
                "redirect not followed (Location: {location}); point the URL at the final endpoint"
            )
        } else {
            let text = response.text().await.unwrap_or_default();
            text.chars().take(200).collect()
        };
        Err(format!("{} webhook returned {status}: {detail}", self.name))
    }
}

/// Build one alerter per configured `[alerts.<name>]` table.
pub fn build_alerters(cfg: &AlertsConfig) -> Result<Vec<Alerter>, String> {
    let mut out = Vec::new();
    if let Some(slack) = &cfg.slack {
        out.push(Alerter::slack(
            slack.resolve_webhook_url()?,
            slack.timeout_s,
            slack.notify_on.iter().copied().collect(),
        ));
    }
    if let Some(webhook) = &cfg.webhook {
        out.push(Alerter::webhook(
            webhook.resolve_url()?,
            webhook.timeout_s,
            webhook.headers.clone(),
            webhook.notify_on.iter().copied().collect(),
        ));
    }
    Ok(out)
}

/// The canonical envelope: `checker_version`, `summary` counts, and the transitions.
fn webhook_payload(transitions: &[Transition]) -> Value {
    #[derive(Serialize)]
    struct Envelope<'a> {
        checker_version: &'a str,
        summary: Summary,
        transitions: &'a [Transition],
    }
    #[derive(Serialize)]
    struct Summary {
        failures: usize,
        recoveries: usize,
    }
    let (failures, recoveries) = counts(transitions);
    serde_json::to_value(Envelope {
        checker_version: crate::VERSION,
        summary: Summary {
            failures,
            recoveries,
        },
        transitions,
    })
    .expect("transitions serialize")
}

fn counts(transitions: &[Transition]) -> (usize, usize) {
    let failures = transitions
        .iter()
        .filter(|t| t.kind == TransitionKind::Failure)
        .count();
    (failures, transitions.len() - failures)
}

/// Block Kit: a header with the counts, then one attachment per transition for its color stripe.
fn slack_payload(transitions: &[Transition]) -> Value {
    let (failures, recoveries) = counts(transitions);
    let failure_word = if failures == 1 { "failure" } else { "failures" };
    let recovery_word = if recoveries == 1 {
        "recovery"
    } else {
        "recoveries"
    };
    let summary =
        format!("chap-checker: {failures} new {failure_word}, {recoveries} {recovery_word}");
    let attachments: Vec<Value> = transitions
        .iter()
        .map(|t| {
            let label = match t.kind {
                TransitionKind::Failure => "FAILURE",
                TransitionKind::Recovery => "RECOVERY",
            };
            let display = t.target_display_name.as_deref().unwrap_or(&t.target_name);
            let body = format!(
                "*{label} \u{2014} `{display}`*\n{}\n`{}`  *{}*  {}",
                t.target_url,
                t.check_name,
                t.current_status.as_str().to_uppercase(),
                t.message
            );
            json!({
                "color": slack_color(t),
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": body}}],
            })
        })
        .collect();
    json!({
        "text": summary,
        "blocks": [{"type": "header", "text": {"type": "plain_text", "text": summary}}],
        "attachments": attachments,
    })
}

/// Slack's brand colors: green for a recovery, yellow for WARN, red otherwise.
fn slack_color(t: &Transition) -> &'static str {
    const GREEN: &str = "#2EB67D";
    const YELLOW: &str = "#ECB22E";
    const RED: &str = "#E01E5A";
    if t.kind == TransitionKind::Recovery {
        return GREEN;
    }
    match t.current_status {
        Status::Ok => GREEN,
        Status::Warn => YELLOW,
        _ => RED,
    }
}

/// Compute and deliver transitions for every configured alerter, then save state.
///
/// Each alerter gets a receiver spec: its `notify_on`, and the targets that opted into it. The
/// state file lock is held across load, compute, dispatch and save. When any delivery fails, the
/// new state is not saved, so the next run sends the same transitions again. Delivery and
/// state-write failures are logged; they never change the run's exit code.
pub async fn dispatch(
    reports: &[RunReport],
    targets: &[TargetEntry],
    alerters: &[Alerter],
    state_path: &Path,
) {
    if alerters.is_empty() {
        return;
    }
    let specs: BTreeMap<String, ReceiverSpec> = alerters
        .iter()
        .map(|a| {
            let opted = targets
                .iter()
                .filter(|t| t.alerts.iter().any(|n| n == a.name))
                .map(|t| t.name.clone())
                .collect();
            (
                a.name.to_owned(),
                ReceiverSpec {
                    notify_on: a.notify_on.clone(),
                    targets: Some(opted),
                },
            )
        })
        .collect();
    let _lock = match StateLock::acquire(state_path, Duration::from_secs(30)).await {
        Ok(lock) => lock,
        Err(e) => {
            tracing::warn!("{e}; skipping alert dispatch this tick");
            return;
        }
    };
    let previous = load_state(state_path);
    let (by_receiver, new_state) =
        compute_receiver_transitions(&previous, reports, &specs, Utc::now());
    let mut all_ok = true;
    for alerter in alerters {
        let pending = by_receiver
            .get(alerter.name)
            .map(Vec::as_slice)
            .unwrap_or_default();
        if pending.is_empty() {
            continue;
        }
        if let Err(e) = alerter.notify(pending).await {
            all_ok = false;
            tracing::error!("alerter {} failed: {e}", alerter.name);
        }
    }
    if !all_ok {
        tracing::warn!(
            "alerter delivery failed; not saving state so transitions will retry on the next run"
        );
        return;
    }
    if let Err(e) = save_state(state_path, &new_state) {
        tracing::error!(
            "could not write state file at {}; continuing: {e}",
            state_path.display()
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::DateTime;

    fn transition(kind: TransitionKind, current: Status) -> Transition {
        Transition {
            kind,
            target_name: "play".into(),
            target_display_name: Some("Play".into()),
            target_url: "https://play/".into(),
            check_name: "dhis2_ping".into(),
            previous_status: Status::Ok,
            current_status: current,
            message: "boom".into(),
            duration_ms: 1.5,
            occurred_at: DateTime::parse_from_rfc3339("2026-11-17T10:00:00Z")
                .unwrap()
                .with_timezone(&Utc),
        }
    }

    #[test]
    fn slack_shape() {
        let p = slack_payload(&[
            transition(TransitionKind::Failure, Status::Warn),
            transition(TransitionKind::Recovery, Status::Ok),
        ]);
        assert_eq!(p["text"], "chap-checker: 1 new failure, 1 recovery");
        assert_eq!(p["attachments"][0]["color"], "#ECB22E");
        assert_eq!(p["attachments"][1]["color"], "#2EB67D");
        assert_eq!(
            p["attachments"][0]["blocks"][0]["text"]["text"],
            "*FAILURE \u{2014} `Play`*\nhttps://play/\n`dhis2_ping`  *WARN*  boom"
        );
    }

    #[test]
    fn webhook_shape() {
        let p = webhook_payload(&[transition(TransitionKind::Failure, Status::Fail)]);
        assert_eq!(p["summary"], json!({"failures": 1, "recoveries": 0}));
        assert_eq!(p["transitions"][0]["occurred_at"], "2026-11-17T10:00:00Z");
        assert_eq!(p["transitions"][0]["kind"], "failure");
    }
}
