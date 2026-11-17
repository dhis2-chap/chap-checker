//! The `http` plugin's check: any URL, judged by expectations written in the config.
//!
//! An instance of `kind = "http"` names a URL and, optionally, what it must answer: the status
//! (`expect_status`, by default any 2xx), text in the body (`body_contains`), or a value at a
//! dotted path in a JSON body (`json_path` with `json_equals`). Redirects are followed, up to five,
//! and the status is the final one's. No credentials are sent unless the config adds headers.

use std::time::Instant;

use reqwest::redirect::Policy;
use serde::Deserialize;
use serde_json::Value;

use super::{CheckContext, CheckDef, CheckResult, Status, details};
use crate::http::{RequestError, TargetKind, build_http};

/// What an `http` instance expects; carried in the target's params.
#[derive(Debug, Clone, Default, Deserialize, serde::Serialize, PartialEq)]
pub struct Expect {
    #[serde(default)]
    pub expect_status: Option<Vec<u16>>,
    #[serde(default)]
    pub body_contains: Option<String>,
    #[serde(default)]
    pub json_path: Option<String>,
    #[serde(default)]
    pub json_equals: Option<Value>,
    #[serde(default)]
    pub headers: std::collections::BTreeMap<String, String>,
}

pub static EXPECT: CheckDef = CheckDef {
    name: "http_expect",
    description: "URL answers with the expected status (default 2xx), and the body or JSON value the config asks for.",
    order: 10,
    requires: &[],
    kinds: &[TargetKind::Http],
    run: |ctx| Box::pin(run(ctx)),
};

async fn run(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "http_expect";
    let expect: Expect = serde_json::from_value(ctx.target.params.clone()).unwrap_or_default();
    let url = ctx.target.url_string();
    let start = Instant::now();
    let outcome = async {
        let http = build_http(
            ctx.target.timeout_s,
            ctx.target.verify_tls,
            Policy::limited(5),
        )?;
        let mut request = http.get(&url);
        for (name, value) in &expect.headers {
            request = request.header(name, value);
        }
        let response = request
            .send()
            .await
            .map_err(|e| RequestError::from_reqwest(&e))?;
        let status = response.status().as_u16();
        let final_url = response.url().to_string();
        let body = response
            .bytes()
            .await
            .map_err(|e| RequestError::from_reqwest(&e))?;
        Ok::<_, RequestError>((status, final_url, body))
    }
    .await;
    let (status, final_url, body) = match outcome {
        Ok(parts) => parts,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(&url))).since(start);
        }
    };
    let mut d = details([
        ("http_status", status.into()),
        ("final_url", final_url.clone().into()),
    ]);
    let fail = |message: String, d| {
        CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .since(start)
    };

    let status_ok = match &expect.expect_status {
        Some(list) => list.contains(&status),
        None => (200..300).contains(&status),
    };
    if !status_ok {
        let wanted = match &expect.expect_status {
            Some(list) => list
                .iter()
                .map(u16::to_string)
                .collect::<Vec<_>>()
                .join(" or "),
            None => "2xx".to_owned(),
        };
        return fail(
            format!("Unexpected status {status} from {final_url} (expected {wanted})."),
            d,
        );
    }
    let mut found = Vec::new();
    if let Some(text) = &expect.body_contains {
        if !String::from_utf8_lossy(&body).contains(text.as_str()) {
            return fail(
                format!("{final_url} answered {status} but the body does not contain {text:?}."),
                d,
            );
        }
        found.push("body matches".to_owned());
    }
    if let Some(path) = &expect.json_path {
        let Ok(json) = serde_json::from_slice::<Value>(&body) else {
            return fail(
                format!("{final_url} answered {status} but the body is not JSON."),
                d,
            );
        };
        let value = path.split('.').try_fold(&json, |v, key| match v {
            Value::Object(o) => o.get(key),
            Value::Array(a) => key.parse::<usize>().ok().and_then(|i| a.get(i)),
            _ => None,
        });
        let Some(value) = value else {
            return fail(
                format!("{final_url} answered {status} but has no '{path}' in its JSON."),
                d,
            );
        };
        d.insert("json_value".into(), value.clone());
        if let Some(wanted) = &expect.json_equals
            && !json_matches(value, wanted)
        {
            return fail(
                format!("'{path}' is {} - expected {}.", show(value), show(wanted)),
                d,
            );
        }
        found.push(format!("{path} = {}", show(value)));
    }
    let suffix = if found.is_empty() {
        String::new()
    } else {
        format!("; {}", found.join(", "))
    };
    CheckResult::new(
        NAME,
        Status::Ok,
        format!("{status} from {final_url}{suffix}"),
    )
    .details(d)
    .since(start)
}

/// Equal as JSON, or as text when one side is a string (`"200"` matches `200`).
fn json_matches(value: &Value, wanted: &Value) -> bool {
    value == wanted || show(value) == show(wanted)
}

fn show(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matching() {
        assert!(json_matches(&json!("success"), &json!("success")));
        assert!(json_matches(&json!(200), &json!("200")));
        assert!(json_matches(&json!(true), &json!(true)));
        assert!(!json_matches(&json!("degraded"), &json!("success")));
    }
}
