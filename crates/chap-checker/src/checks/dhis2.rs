//! Checks against a DHIS2 instance and the chap-core integration reached through it.

use std::time::Instant;

use serde_json::{Map, Value};

use super::{
    CheckContext, CheckDef, CheckResult, Status, details, diagnose_status, non_empty_str,
    parse_dhis2_version, without_nulls,
};
use crate::http::{Response, TargetKind};

const DHIS2: &[TargetKind] = &[TargetKind::Dhis2];

// ---------------------------------------------------------------------------------------------
// dhis2_ping

pub static PING: CheckDef = CheckDef {
    name: "dhis2_ping",
    description: "DHIS2 server reachable and credentials accepted.",
    order: 10,
    requires: &[],
    kinds: DHIS2,
    run: |ctx| Box::pin(ping(ctx)),
};

async fn ping(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "dhis2_ping";
    const PATH: &str = "/api/me";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let fail = |message: &str| CheckResult::new(NAME, Status::Fail, message).since(start);
    if response.status == 401 {
        return fail("Authentication rejected (401).");
    }
    if response.status >= 400 {
        return fail(&format!("Unexpected status {}.", response.status));
    }
    // A 2xx HTML page (typical SSO / reverse-proxy login interception) is not proof of DHIS2 auth.
    if !response
        .content_type
        .as_deref()
        .unwrap_or("")
        .starts_with("application/json")
    {
        return fail(
            "/api/me responded with a non-JSON body - likely an SSO or proxy login page intercepted the request. \
             The DHIS2 server itself was not reached.",
        );
    }
    let Some(body) = response.json() else {
        return fail("Server returned malformed JSON for /api/me.");
    };
    let Some(body) = body.as_object() else {
        return fail("Unexpected /api/me response shape (expected a JSON object).");
    };
    // Real DHIS2 always populates at least one of `username` or `id`.
    let username = body.get("username").filter(|v| truthy(v)).cloned();
    let user_id = body.get("id").filter(|v| truthy(v)).cloned();
    let who = match (&username, &user_id) {
        (Some(u), _) => display(u),
        (None, Some(i)) => display(i),
        (None, None) => {
            return fail(
                "/api/me returned JSON with neither 'username' nor 'id' - the response is not from DHIS2.",
            );
        }
    };
    CheckResult::new(NAME, Status::Ok, format!("Authenticated as {who}."))
        .details(details([
            (
                "username",
                body.get("username").cloned().unwrap_or(Value::Null),
            ),
            ("user_id", body.get("id").cloned().unwrap_or(Value::Null)),
        ]))
        .since(start)
}

// ---------------------------------------------------------------------------------------------
// dhis2_system_info

pub static SYSTEM_INFO: CheckDef = CheckDef {
    name: "dhis2_system_info",
    description: "DHIS2 /api/system/info reachable and reports a version.",
    order: 20,
    requires: &["dhis2_ping"],
    kinds: DHIS2,
    run: |ctx| Box::pin(system_info(ctx)),
};

async fn system_info(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "dhis2_system_info";
    const PATH: &str = "/api/system/info";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let elapsed = ms(start);
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some(
            "/api/system/info returned 404 - this server does not look like a DHIS2 instance \
             (or sits behind a proxy that doesn't expose the API).",
        ),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    let Some(body) = response.json() else {
        return CheckResult::new(NAME, Status::Fail, "DHIS2 responded but body was not JSON.")
            .took(elapsed);
    };
    let Some(body) = body.as_object() else {
        return CheckResult::new(
            NAME,
            Status::Fail,
            "Unexpected /api/system/info response shape (expected a JSON object).",
        )
        .took(elapsed);
    };
    let mut info = without_nulls(body);
    let Some(version) = non_empty_str(body, "version") else {
        info.insert("raw".into(), Value::Object(body.clone()));
        return CheckResult::new(
            NAME,
            Status::Warn,
            "DHIS2 reachable but no 'version' field in response.",
        )
        .details(info)
        .took(elapsed);
    };
    // Later checks can read the detected version from the context.
    ctx.dhis2_version = parse_dhis2_version(version);
    let revision = non_empty_str(body, "revision")
        .map(|r| format!(" (rev {r})"))
        .unwrap_or_default();
    CheckResult::new(NAME, Status::Ok, format!("DHIS2 {version}{revision}."))
        .details(info)
        .took(elapsed)
}

// ---------------------------------------------------------------------------------------------
// dhis2_chap_route

pub static CHAP_ROUTE: CheckDef = CheckDef {
    name: "dhis2_chap_route",
    description: "DHIS2 route '/api/routes/chap' exists and is enabled.",
    order: 30,
    requires: &["dhis2_ping"],
    kinds: DHIS2,
    run: |ctx| Box::pin(chap_route(ctx)),
};

async fn chap_route(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "dhis2_chap_route";
    const PATH: &str = "/api/routes";
    const CODE: &str = "chap";
    let start = Instant::now();
    let params = [
        ("fields", "id,code,name,url,disabled"),
        ("filter", "code:eq:chap"),
        ("paging", "false"),
    ];
    let response = match ctx.client.get_with(PATH, &params, &[]).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let elapsed = ms(start);
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some(
            "/api/routes returned 404 - the routes API is missing on this server \
             (routes were introduced in DHIS2 2.40; older versions do not expose this endpoint).",
        ),
        Some("F_ROUTE_READ_PRIVATE"),
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    let fail = |message: String| CheckResult::new(NAME, Status::Fail, message).took(elapsed);
    let Some(body) = response.json() else {
        return fail("DHIS2 returned malformed JSON when listing routes.".into());
    };
    let routes = match route_list(&body) {
        Ok(routes) => routes,
        Err(count) => {
            return fail(format!(
                "Unexpected shape from {PATH}: {count} validation error(s)."
            ));
        }
    };
    let Some(route) = routes
        .iter()
        .find(|r| r.get("code").and_then(Value::as_str) == Some(CODE))
    else {
        return CheckResult::new(
            NAME,
            Status::Fail,
            format!("No DHIS2 route with code '{CODE}'."),
        )
        .details(details([("route_count", routes.len().into())]))
        .took(elapsed);
    };
    let route_details = without_nulls(route);
    if route.get("disabled").and_then(Value::as_bool) == Some(true) {
        return CheckResult::new(
            NAME,
            Status::Warn,
            format!("Route '{CODE}' exists but is disabled."),
        )
        .details(route_details)
        .took(elapsed);
    }
    let url = non_empty_str(route, "url").unwrap_or("?");
    CheckResult::new(
        NAME,
        Status::Ok,
        format!("Route '{CODE}' present -> {url}."),
    )
    .details(route_details)
    .took(elapsed)
}

/// The `routes` entries of a `/api/routes` body, or the number of shape problems found.
fn route_list(body: &Value) -> Result<Vec<Map<String, Value>>, usize> {
    let Some(object) = body.as_object() else {
        return Err(1);
    };
    let Some(routes) = object.get("routes") else {
        return Ok(Vec::new());
    };
    let Some(routes) = routes.as_array() else {
        return Err(1);
    };
    let mut problems = 0;
    let mut out = Vec::new();
    for route in routes {
        let Some(route) = route.as_object() else {
            problems += 1;
            continue;
        };
        for key in ["id", "code", "name", "url"] {
            if route
                .get(key)
                .is_some_and(|v| !v.is_null() && !v.is_string())
            {
                problems += 1;
            }
        }
        if route
            .get("disabled")
            .is_some_and(|v| !v.is_null() && !v.is_boolean())
        {
            problems += 1;
        }
        out.push(route.clone());
    }
    if problems > 0 { Err(problems) } else { Ok(out) }
}

// ---------------------------------------------------------------------------------------------
// dhis2_chap_ping

pub static CHAP_PING: CheckDef = CheckDef {
    name: "dhis2_chap_ping",
    description: "chap-core /health reachable through the chap route.",
    order: 40,
    requires: &["dhis2_chap_route"],
    kinds: DHIS2,
    run: |ctx| Box::pin(chap_ping(ctx)),
};

async fn chap_ping(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "dhis2_chap_ping";
    const PATH: &str = "/api/routes/chap/run/health";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let elapsed = ms(start);
    if response.status == 502 {
        // The route is wired but chap-core upstream didn't answer.
        return CheckResult::new(
            NAME,
            Status::Fail,
            "DHIS2 route returned 502 - chap-core did not respond.",
        )
        .details(details([
            ("http_status", 502.into()),
            ("path", PATH.into()),
        ]))
        .took(elapsed);
    }
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some(
            "/api/routes/chap/run/health returned 404 - the chap route exists but the upstream chap-core has no /health endpoint.",
        ),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    chap_health_body(NAME, PATH, &response, elapsed)
}

/// Judge a chap-core `/health` body: a JSON object whose `status` is `success`.
///
/// Shared by `dhis2_chap_ping` (through the DHIS2 route) and the `chap` plugin (direct).
pub(crate) fn chap_health_body(
    name: &'static str,
    path: &str,
    response: &Response,
    elapsed: f64,
) -> CheckResult {
    // A 2xx alone is not proof chap-core answered: an SSO or reverse proxy can return a 200 login
    // page. Anything but a JSON object carrying chap-core's `status` field is a FAIL.
    let fail = |message: String, extra: Map<String, Value>| {
        let mut d = details([
            ("http_status", response.status.into()),
            ("path", path.into()),
        ]);
        d.extend(extra);
        CheckResult::new(name, Status::Fail, message)
            .details(d)
            .took(elapsed)
    };
    let content_type = response.content_type.clone().unwrap_or_default();
    if !content_type.is_empty() && !content_type.to_lowercase().contains("json") {
        let essence = content_type.split(';').next().unwrap_or("").trim();
        return fail(
            format!(
                "{path} responded with a non-JSON body ({essence}) - likely an SSO or proxy login page \
                 intercepted the request. chap-core was not reached."
            ),
            details([("content_type", content_type.clone().into())]),
        );
    }
    let Some(body) = response.json() else {
        return fail(
            format!("{path} returned malformed JSON - the response is not from chap-core."),
            Map::new(),
        );
    };
    let Some(health) = body
        .as_object()
        .and_then(|b| b.get("status"))
        .and_then(Value::as_str)
    else {
        return fail(
            format!(
                "Unexpected {path} response shape (expected a JSON object with a 'status' field) - \
                 the response is not from chap-core."
            ),
            Map::new(),
        );
    };
    let message = body.get("message").and_then(Value::as_str);
    let mut d = details([("health_status", health.into())]);
    if let Some(m) = message {
        d.insert("health_message".into(), m.into());
    }
    if health != "success" {
        let suffix = message
            .filter(|m| !m.is_empty())
            .map(|m| format!(": {m}"))
            .unwrap_or_default();
        return fail(
            format!("chap-core reported health status '{health}'{suffix}"),
            d,
        );
    }
    CheckResult::new(
        name,
        Status::Ok,
        format!("chap-core healthy (status {}).", response.status),
    )
    .details(d)
    .took(elapsed)
}

// ---------------------------------------------------------------------------------------------
// dhis2_chap_system_info

pub static CHAP_SYSTEM_INFO: CheckDef = CheckDef {
    name: "dhis2_chap_system_info",
    description: "chap-core /system/info reachable via the chap route and reports a version.",
    order: 50,
    requires: &["dhis2_chap_ping"],
    kinds: DHIS2,
    run: |ctx| Box::pin(chap_system_info(ctx)),
};

async fn chap_system_info(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "dhis2_chap_system_info";
    const PATH: &str = "/api/routes/chap/run/system/info";
    // chap-core 1.1.x - 1.2.0 served it at `/system-info`; tried only when the current path 404s.
    const LEGACY_PATH: &str = "/api/routes/chap/run/system-info";
    let start = Instant::now();
    let mut response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    if response.status == 404 {
        // A failed fallback is reported as the original 404.
        if let Ok(legacy) = ctx.client.get(LEGACY_PATH).await
            && (200..300).contains(&legacy.status)
        {
            response = legacy;
        }
    }
    let elapsed = ms(start);
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some(
            "/api/routes/chap/run/system/info returned 404 - either the chap route is missing \
             or the upstream chap-core has no /system/info endpoint.",
        ),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    chap_system_info_body(NAME, &response, elapsed)
}

/// Judge a chap-core `/system/info` body: its version, revision and Python version.
///
/// Shared by `dhis2_chap_system_info` (through the DHIS2 route) and the `chap` plugin (direct).
pub(crate) fn chap_system_info_body(
    name: &'static str,
    response: &Response,
    elapsed: f64,
) -> CheckResult {
    let Some(body) = response.json() else {
        return CheckResult::new(
            name,
            Status::Fail,
            "chap-core responded but body was not JSON.",
        )
        .took(elapsed);
    };
    let Some(body) = body.as_object() else {
        return CheckResult::new(
            name,
            Status::Fail,
            "Unexpected chap-core /system/info response shape (expected a JSON object).",
        )
        .took(elapsed);
    };
    let mut info = without_nulls(body);
    let Some(version) = non_empty_str(body, "chap_core_version") else {
        info.insert("raw".into(), Value::Object(body.clone()));
        return CheckResult::new(
            name,
            Status::Warn,
            "chap-core reachable but no 'version' field in response.",
        )
        .details(info)
        .took(elapsed);
    };
    let mut parts = vec![format!("chap-core {version}")];
    if let Some(revision) = non_empty_str(body, "revision") {
        parts.push(format!("rev {revision}"));
    }
    if let Some(python) = non_empty_str(body, "python_version") {
        parts.push(format!("python {python}"));
    }
    CheckResult::new(name, Status::Ok, format!("{}.", parts.join(" / ")))
        .details(info)
        .took(elapsed)
}

// ---------------------------------------------------------------------------------------------
// dhis2_chap_modeling_app, dhis2_chap_climate_app

const MODELING_APP_HUB_ID: &str = "a29851f9-82a7-4ecd-8b2c-58e0f220bc75";
const CLIMATE_APP_HUB_ID: &str = "effb986c-a3c7-485e-a2f6-5e54ff9df7c3";

pub static MODELING_APP: CheckDef = CheckDef {
    name: "dhis2_chap_modeling_app",
    description: "DHIS2 app with app_hub_id of the modeling app is installed and reports a version.",
    order: 60,
    // On a plain DHIS2 instance without chap-core the app is never installed, so skipping on a
    // missing route is more honest than failing every poll.
    requires: &["dhis2_chap_route"],
    kinds: DHIS2,
    run: |ctx| {
        Box::pin(app_check(
            ctx,
            "dhis2_chap_modeling_app",
            MODELING_APP_HUB_ID,
            "modeling-app",
        ))
    },
};

pub static CLIMATE_APP: CheckDef = CheckDef {
    name: "dhis2_chap_climate_app",
    description: "DHIS2 app with app_hub_id of the climate app is installed and reports a version.",
    order: 70,
    requires: &["dhis2_chap_route"],
    kinds: DHIS2,
    run: |ctx| {
        Box::pin(app_check(
            ctx,
            "dhis2_chap_climate_app",
            CLIMATE_APP_HUB_ID,
            "climate-app",
        ))
    },
};

/// Look for an installed app whose App Hub UUID is `hub_id`.
async fn app_check(
    ctx: &mut CheckContext,
    name: &'static str,
    hub_id: &str,
    fallback: &str,
) -> CheckResult {
    const PATH: &str = "/api/apps";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(name, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let elapsed = ms(start);
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some(
            "/api/apps returned 404 - the apps endpoint is missing on this server \
             (unexpected on DHIS2; check the URL / reverse-proxy config).",
        ),
        Some("M_dhis-web-app-management"),
    ) {
        return CheckResult::new(name, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    let fail = |message: String| CheckResult::new(name, Status::Fail, message).took(elapsed);
    let Some(body) = response.json() else {
        return fail("DHIS2 returned malformed JSON when listing apps.".into());
    };
    let entries: Vec<Value> = match &body {
        Value::Array(items) => items.clone(),
        Value::Object(o) => match o.get("apps") {
            None => Vec::new(),
            Some(Value::Array(items)) => items.clone(),
            Some(_) => {
                return fail(
                    "Unexpected /api/apps entry shape: Input should be a valid list".into(),
                );
            }
        },
        _ => {
            return fail("Unexpected /api/apps response shape (expected a list or object).".into());
        }
    };
    let mut apps = Vec::with_capacity(entries.len());
    for entry in &entries {
        let Some(entry) = entry.as_object() else {
            return fail("Unexpected /api/apps entry shape (entries must be objects).".into());
        };
        match normalize_app(entry) {
            Ok(app) => apps.push(app),
            Err(problem) => return fail(format!("Unexpected /api/apps entry shape: {problem}")),
        }
    }
    let Some(app) = apps
        .iter()
        .find(|a| a.get("appHubId").and_then(Value::as_str) == Some(hub_id))
    else {
        return CheckResult::new(
            name,
            Status::Fail,
            format!("No app with app_hub_id '{hub_id}' installed."),
        )
        .details(details([("app_count", apps.len().into())]))
        .took(elapsed);
    };
    let label = non_empty_str(app, "name")
        .or_else(|| non_empty_str(app, "key"))
        .unwrap_or(fallback);
    match non_empty_str(app, "version") {
        None => CheckResult::new(
            name,
            Status::Warn,
            format!("{label} installed but no 'version' field."),
        )
        .details(app.clone())
        .took(elapsed),
        Some(version) => CheckResult::new(name, Status::Ok, format!("{label} {version}."))
            .details(app.clone())
            .took(elapsed),
    }
}

/// One `/api/apps` entry with its known fields under their camelCase names and no nulls.
///
/// DHIS2 returns `app_hub_id` on some versions and `appHubId` on others; both are accepted.
fn normalize_app(entry: &Map<String, Value>) -> Result<Map<String, Value>, String> {
    const RENAMES: [(&str, &str); 3] = [
        ("app_hub_id", "appHubId"),
        ("app_type", "appType"),
        ("launch_url", "launchUrl"),
    ];
    let mut out = entry.clone();
    for (snake, camel) in RENAMES {
        if !out.contains_key(camel) {
            if let Some(value) = out.remove(snake) {
                out.insert(camel.to_owned(), value);
            }
        } else {
            out.remove(snake);
        }
    }
    for key in ["key", "name", "version", "appHubId", "appType", "launchUrl"] {
        if out.get(key).is_some_and(|v| !v.is_null() && !v.is_string()) {
            return Err(format!("{key}: Input should be a valid string"));
        }
    }
    Ok(without_nulls(&out))
}

// ---------------------------------------------------------------------------------------------

fn ms(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

/// Python truthiness for a JSON value.
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

/// A JSON value as Python's `str()` would print it in a message.
fn display(value: &Value) -> String {
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
    fn apps_accept_both_spellings() {
        let snake = json!({"key": "k", "app_hub_id": "x", "launch_url": null});
        let app = normalize_app(snake.as_object().unwrap()).unwrap();
        assert_eq!(Value::Object(app), json!({"key": "k", "appHubId": "x"}));
        let wrong = json!({"version": 3});
        assert!(normalize_app(wrong.as_object().unwrap()).is_err());
    }

    #[test]
    fn routes_shape() {
        assert_eq!(route_list(&json!([])), Err(1));
        assert_eq!(route_list(&json!({})).unwrap().len(), 0);
        assert_eq!(route_list(&json!({"routes": [{"code": 1}]})), Err(1));
    }
}
