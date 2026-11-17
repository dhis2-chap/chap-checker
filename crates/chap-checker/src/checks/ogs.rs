//! Checks against an Open Geo Service (openEO backend) deployment.

use std::time::Instant;

use serde_json::{Map, Value};

use super::{CheckContext, CheckDef, CheckResult, Status, details, diagnose_status};
use crate::http::TargetKind;

const OGS: &[TargetKind] = &[TargetKind::Ogs];

/// Liveness only: upstream `/health` says the API process is up and does not probe storage or
/// the job service. `degraded` is WARN; `unhealthy` or anything else is FAIL.
pub static HEALTH: CheckDef = CheckDef {
    name: "ogs_health",
    description: "Open Geo Service /health reports healthy (liveness only).",
    order: 10,
    requires: &[],
    kinds: OGS,
    run: |ctx| Box::pin(health(ctx)),
};

async fn health(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "ogs_health";
    const PATH: &str = "/health";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let elapsed = start.elapsed().as_secs_f64() * 1000.0;
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some("/health returned 404 - this server does not look like an Open Geo Service."),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    let Some(body) = response.json() else {
        return CheckResult::new(
            NAME,
            Status::Fail,
            "/health responded with a non-JSON body - a proxy or login page may be in front of OGS.",
        )
        .took(elapsed);
    };
    let reported = match body.as_object().and_then(|b| b.get("status")) {
        None => String::new(),
        Some(Value::String(s)) => s.to_lowercase(),
        Some(Value::Null) => "none".to_owned(),
        Some(other) => other.to_string().to_lowercase(),
    };
    let d = details([(
        "health_status",
        if reported.is_empty() {
            Value::Null
        } else {
            reported.clone().into()
        },
    )]);
    let (status, message) = match reported.as_str() {
        "healthy" => (Status::Ok, "Service healthy.".to_owned()),
        "degraded" => (Status::Warn, "Service reports degraded.".to_owned()),
        "" => (
            Status::Fail,
            "/health body has no 'status' field.".to_owned(),
        ),
        other => (Status::Fail, format!("Service reports status '{other}'.")),
    };
    CheckResult::new(NAME, status, message)
        .details(d)
        .took(elapsed)
}

/// Reads the version from `/info` and the openEO capabilities from `/`. `details["version"]` is
/// what the dashboard shows on the tile; `read_only` is reported because public demo deployments
/// run read-only on purpose.
pub static INFO: CheckDef = CheckDef {
    name: "ogs_info",
    description: "Open Geo Service /info and openEO capabilities readable.",
    order: 20,
    requires: &["ogs_health"],
    kinds: OGS,
    run: |ctx| Box::pin(info(ctx)),
};

async fn info(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "ogs_info";
    let start = Instant::now();
    let mut bodies: Vec<Map<String, Value>> = Vec::with_capacity(2);
    for path in ["/info", "/"] {
        // Ask for JSON explicitly: `GET /` serves an HTML landing page to clients preferring HTML.
        let response = match ctx
            .client
            .get_with(path, &[], &[("Accept", "application/json")])
            .await
        {
            Ok(r) => r,
            Err(e) => {
                return CheckResult::new(NAME, Status::Error, e.describe(Some(path))).since(start);
            }
        };
        if let Some((message, d)) = diagnose_status(response.status, path, None, None) {
            return CheckResult::new(NAME, Status::Fail, message)
                .details(d)
                .since(start);
        }
        match response.json() {
            Some(Value::Object(body)) => bodies.push(body),
            _ => {
                return CheckResult::new(
                    NAME,
                    Status::Fail,
                    format!("{path} did not return a JSON object."),
                )
                .since(start);
            }
        }
    }
    let elapsed = start.elapsed().as_secs_f64() * 1000.0;
    let (info, capabilities) = (&bodies[0], &bodies[1]);
    let get = |o: &Map<String, Value>, k: &str| o.get(k).cloned().unwrap_or(Value::Null);
    let version = [
        get(info, "app_version"),
        get(capabilities, "backend_version"),
    ]
    .into_iter()
    .find(truthy)
    .unwrap_or_else(|| get(capabilities, "backend_version"));
    let api_version = get(capabilities, "api_version");
    let read_only = get(info, "read_only");
    let mut qualifiers = Vec::new();
    if truthy(&api_version) {
        qualifiers.push(format!("openEO {}", text(&api_version)));
    }
    if read_only == Value::Bool(true) {
        qualifiers.push("read-only".to_owned());
    }
    let suffix = if qualifiers.is_empty() {
        String::new()
    } else {
        format!(" ({})", qualifiers.join(", "))
    };
    let shown = if truthy(&version) {
        text(&version)
    } else {
        "unknown version".to_owned()
    };
    CheckResult::new(
        NAME,
        Status::Ok,
        format!("Open Geo Service {shown}{suffix}."),
    )
    .details(details([
        ("version", version),
        ("api_version", api_version),
        ("backend_id", get(capabilities, "id")),
        ("read_only", read_only),
    ]))
    .took(elapsed)
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}
