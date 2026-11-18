//! Checks, the runner and alert dispatch against a mock server.

use std::collections::BTreeSet;

use chap_checker::alerts::{Alerter, dispatch};
use chap_checker::checks::{CheckResult, Status, resolve_checks};
use chap_checker::http::{Auth, RetryPolicy, Target, TargetKind};
use chap_checker::runner::{RunReport, TargetEntry, run_checks, run_targets};
use chap_checker::state_store::load_state;
use serde_json::json;
use url::Url;
use wiremock::matchers::{header, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn target(server: &MockServer, kind: TargetKind) -> Target {
    Target {
        kind,
        base_url: Url::parse(&server.uri()).unwrap(),
        timeout_s: 5.0,
        verify_tls: true,
        auth: (kind == TargetKind::Dhis2).then(|| Auth::Token("pat".into())),
        retry: None,
        params: serde_json::Value::Null,
    }
}

fn ok_json(body: serde_json::Value) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_json(body)
}

async fn mount(server: &MockServer, at: &str, response: ResponseTemplate) {
    Mock::given(method("GET"))
        .and(path(at))
        .respond_with(response)
        .mount(server)
        .await;
}

/// A DHIS2 with the chap-core integration fully working.
async fn healthy_dhis2() -> MockServer {
    let server = MockServer::start().await;
    mount(&server, "/", ResponseTemplate::new(200)).await;
    Mock::given(method("GET"))
        .and(path("/api/me"))
        .and(header("Authorization", "ApiToken pat"))
        .respond_with(ok_json(json!({"username": "admin", "id": "xE7jOejl9FI"})))
        .mount(&server)
        .await;
    mount(
        &server,
        "/api/system/info",
        ok_json(json!({"version": "2.42.3", "revision": "abc", "buildTime": null})),
    )
    .await;
    Mock::given(method("GET"))
        .and(path("/api/routes"))
        .and(query_param("filter", "code:eq:chap"))
        .respond_with(ok_json(json!({"routes": [{"id": "r1", "code": "chap", "url": "http://chap:8000/**", "disabled": false}]})))
        .mount(&server)
        .await;
    mount(
        &server,
        "/api/routes/chap/run/health",
        ok_json(json!({"status": "success", "message": "ok"})),
    )
    .await;
    mount(
        &server,
        "/api/routes/chap/run/system/info",
        ok_json(json!({"chap_core_version": "1.4.0", "python_version": "3.13"})),
    )
    .await;
    mount(
        &server,
        "/api/apps",
        ok_json(json!([
            {"key": "modeling", "name": "Modeling", "version": "2.1.0", "app_hub_id": "a29851f9-82a7-4ecd-8b2c-58e0f220bc75"},
            {"key": "climate", "appHubId": "effb986c-a3c7-485e-a2f6-5e54ff9df7c3"},
        ])),
    )
    .await;
    server
}

async fn run(target: &Target) -> Vec<CheckResult> {
    let checks = resolve_checks(None, target.kind).unwrap();
    run_checks(target, &checks).await
}

fn statuses(results: &[CheckResult]) -> Vec<(&str, Status)> {
    results
        .iter()
        .map(|r| (r.name.as_str(), r.status))
        .collect()
}

#[tokio::test]
async fn dhis2_all_green_except_versionless_app() {
    let server = healthy_dhis2().await;
    let results = run(&target(&server, TargetKind::Dhis2)).await;
    assert_eq!(
        statuses(&results),
        [
            ("http_2xx", Status::Ok),
            ("dhis2_ping", Status::Ok),
            ("dhis2_system_info", Status::Ok),
            ("dhis2_chap_route", Status::Ok),
            ("dhis2_chap_ping", Status::Ok),
            ("dhis2_chap_system_info", Status::Ok),
            ("dhis2_chap_modeling_app", Status::Ok),
            ("dhis2_chap_climate_app", Status::Warn),
        ]
    );
    assert_eq!(results[1].message, "Authenticated as admin.");
    assert_eq!(results[2].message, "DHIS2 2.42.3 (rev abc).");
    assert_eq!(
        results[2].details.get("buildTime"),
        None,
        "nulls are dropped"
    );
    assert_eq!(
        results[3].message,
        "Route 'chap' present -> http://chap:8000/**."
    );
    assert_eq!(results[5].message, "chap-core 1.4.0 / python 3.13.");
    assert_eq!(results[6].message, "Modeling 2.1.0.");
    assert_eq!(
        results[6].details["appHubId"],
        "a29851f9-82a7-4ecd-8b2c-58e0f220bc75"
    );
    assert_eq!(
        results[7].message,
        "climate installed but no 'version' field."
    );
}

#[tokio::test]
async fn login_page_fails_ping_and_skips_the_rest() {
    let server = MockServer::start().await;
    mount(&server, "/", ResponseTemplate::new(200)).await;
    mount(
        &server,
        "/api/me",
        ResponseTemplate::new(200).set_body_raw("<html>login</html>", "text/html"),
    )
    .await;
    let results = run(&target(&server, TargetKind::Dhis2)).await;
    assert_eq!(results[1].status, Status::Fail);
    assert!(results[1].message.contains("SSO or proxy login page"));
    assert_eq!(results[2].status, Status::Skipped);
    assert_eq!(results[2].message, "Skipped: dhis2_ping not OK.");
    assert_eq!(results[4].message, "Skipped: dhis2_chap_route not OK.");
}

#[tokio::test]
async fn forbidden_apps_names_the_authority() {
    let server = healthy_dhis2().await;
    server.reset().await;
    mount(&server, "/api/me", ok_json(json!({"id": "u1"}))).await;
    mount(
        &server,
        "/api/routes",
        ok_json(json!({"routes": [{"code": "chap", "disabled": true}]})),
    )
    .await;
    mount(&server, "/api/apps", ResponseTemplate::new(403)).await;
    let checks = resolve_checks(
        Some(&["dhis2_chap_modeling_app".to_owned()]),
        TargetKind::Dhis2,
    )
    .unwrap();
    let results = run_checks(&target(&server, TargetKind::Dhis2), &checks).await;
    assert_eq!(results[0].message, "Authenticated as u1.");
    assert_eq!(results[1].status, Status::Warn, "a disabled route warns");
    // WARN is not OK, so the app check is skipped.
    assert_eq!(results[2].status, Status::Skipped);
    mount(
        &server,
        "/api/routes",
        ok_json(json!({"routes": [{"code": "chap"}]})),
    )
    .await;
    server.reset().await;
    mount(&server, "/api/me", ok_json(json!({"id": "u1"}))).await;
    mount(
        &server,
        "/api/routes",
        ok_json(json!({"routes": [{"code": "chap"}]})),
    )
    .await;
    mount(&server, "/api/apps", ResponseTemplate::new(403)).await;
    let results = run_checks(&target(&server, TargetKind::Dhis2), &checks).await;
    assert_eq!(results[2].status, Status::Fail);
    assert_eq!(
        results[2].details["required_authority"],
        "M_dhis-web-app-management"
    );
    assert_eq!(results[2].details["http_status"], 403);
}

#[tokio::test]
async fn chap_ping_502_and_legacy_system_info() {
    let server = healthy_dhis2().await;
    server.reset().await;
    mount(&server, "/api/me", ok_json(json!({"username": "a"}))).await;
    mount(
        &server,
        "/api/routes",
        ok_json(json!({"routes": [{"code": "chap"}]})),
    )
    .await;
    mount(
        &server,
        "/api/routes/chap/run/health",
        ok_json(json!({"status": "success"})),
    )
    .await;
    mount(
        &server,
        "/api/routes/chap/run/system/info",
        ResponseTemplate::new(404),
    )
    .await;
    mount(
        &server,
        "/api/routes/chap/run/system-info",
        ok_json(json!({"chap_core_version": "1.2.0"})),
    )
    .await;
    let checks = resolve_checks(
        Some(&["dhis2_chap_system_info".to_owned()]),
        TargetKind::Dhis2,
    )
    .unwrap();
    let results = run_checks(&target(&server, TargetKind::Dhis2), &checks).await;
    assert_eq!(results[3].message, "chap-core 1.2.0.");

    server.reset().await;
    mount(&server, "/api/me", ok_json(json!({"username": "a"}))).await;
    mount(
        &server,
        "/api/routes",
        ok_json(json!({"routes": [{"code": "chap"}]})),
    )
    .await;
    mount(
        &server,
        "/api/routes/chap/run/health",
        ResponseTemplate::new(502),
    )
    .await;
    let results = run_checks(&target(&server, TargetKind::Dhis2), &checks).await;
    assert_eq!(
        results[2].message,
        "DHIS2 route returned 502 - chap-core did not respond."
    );
    assert_eq!(results[3].status, Status::Skipped);
}

#[tokio::test]
async fn ogs_checks() {
    let server = MockServer::start().await;
    mount(
        &server,
        "/",
        ok_json(json!({"api_version": "1.2.0", "id": "ogs", "backend_version": "0.3"})),
    )
    .await;
    mount(&server, "/health", ok_json(json!({"status": "Healthy"}))).await;
    mount(
        &server,
        "/info",
        ok_json(json!({"app_version": "0.4.1", "read_only": true})),
    )
    .await;
    let results = run(&target(&server, TargetKind::Ogs)).await;
    assert_eq!(
        statuses(&results),
        [
            ("http_2xx", Status::Ok),
            ("ogs_health", Status::Ok),
            ("ogs_info", Status::Ok)
        ]
    );
    assert_eq!(
        results[2].message,
        "Open Geo Service 0.4.1 (openEO 1.2.0, read-only)."
    );

    server.reset().await;
    mount(&server, "/health", ok_json(json!({"status": "degraded"}))).await;
    let results = run(&target(&server, TargetKind::Ogs)).await;
    assert_eq!(results[1].status, Status::Warn);
    assert_eq!(results[2].status, Status::Skipped);
}

#[tokio::test]
async fn transport_errors_are_errors() {
    let unused = Target {
        kind: TargetKind::Ogs,
        base_url: Url::parse("http://127.0.0.1:9/").unwrap(),
        timeout_s: 2.0,
        verify_tls: true,
        auth: None,
        retry: None,
        params: serde_json::Value::Null,
    };
    let results = run(&unused).await;
    assert_eq!(results[1].status, Status::Error);
    // A closed port is refused on Linux and macOS; Windows retries the connect until it times out.
    let m = &results[1].message;
    assert!(
        m.starts_with("ConnectError (/health): ") || m.starts_with("ConnectTimeout (/health): "),
        "{}",
        results[1].message
    );
}

#[tokio::test]
async fn retries_transient_statuses() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/me"))
        .respond_with(ResponseTemplate::new(503))
        .up_to_n_times(2)
        .mount(&server)
        .await;
    mount(&server, "/api/me", ok_json(json!({"username": "a"}))).await;
    let mut t = target(&server, TargetKind::Dhis2);
    t.retry = Some(RetryPolicy {
        base_delay: 0.01,
        jitter: 0.0,
        ..RetryPolicy::default()
    });
    let checks = resolve_checks(Some(&["dhis2_ping".to_owned()]), TargetKind::Dhis2).unwrap();
    assert_eq!(run_checks(&t, &checks).await[0].status, Status::Ok);
    t.retry = None;
    server.reset().await;
    mount(&server, "/api/me", ResponseTemplate::new(503)).await;
    assert_eq!(
        run_checks(&t, &checks).await[0].message,
        "Unexpected status 503."
    );
}

fn entry(name: &str, target: Target, alerts: &[&str]) -> TargetEntry {
    TargetEntry {
        name: name.into(),
        display_name: None,
        target,
        check_names: Some(vec!["ogs_health".into()]),
        alerts: alerts.iter().map(|s| (*s).to_owned()).collect(),
    }
}

#[tokio::test]
async fn dispatch_sends_once_and_keeps_state_on_failure() {
    let ogs = MockServer::start().await;
    mount(&ogs, "/health", ok_json(json!({"status": "unhealthy"}))).await;
    let hook = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/hook"))
        .and(header("X-Key", "k"))
        .respond_with(ResponseTemplate::new(204))
        .expect(1)
        .mount(&hook)
        .await;

    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("state.json");
    let targets = vec![
        entry("a", target(&ogs, TargetKind::Ogs), &["webhook"]),
        entry("b", target(&ogs, TargetKind::Ogs), &[]),
    ];
    let alerter = Alerter::webhook(
        format!("{}/hook", hook.uri()),
        5.0,
        [("X-Key".to_owned(), "k".to_owned())].into(),
        BTreeSet::from([Status::Fail]),
    );
    let reports: Vec<RunReport> = run_targets(&targets, 2).await;
    assert_eq!(reports[0].results[0].status, Status::Fail);
    dispatch(&reports, &targets, std::slice::from_ref(&alerter), &state).await;
    // The same outage again: nothing new to send (the mock expects exactly one POST).
    dispatch(&reports, &targets, std::slice::from_ref(&alerter), &state).await;
    let saved = load_state(&state);
    assert_eq!(saved.states.len(), 2);
    assert_eq!(
        saved.receivers.as_ref().unwrap()["webhook"].len(),
        1,
        "only the opted-in target"
    );
    let posted = &hook.received_requests().await.unwrap()[0];
    let body: serde_json::Value = serde_json::from_slice(&posted.body).unwrap();
    assert_eq!(body["summary"], json!({"failures": 1, "recoveries": 0}));
    hook.verify().await;

    // A receiver that refuses: state is not saved, so the next run sends again.
    let refused = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(500).set_body_string("nope"))
        .mount(&refused)
        .await;
    let other = dir.path().join("other.json");
    let failing = Alerter::webhook(
        refused.uri(),
        5.0,
        Default::default(),
        BTreeSet::from([Status::Fail]),
    );
    let err = failing.notify(&[]).await;
    assert!(err.is_ok(), "an empty list sends nothing");
    dispatch(&reports, &targets, std::slice::from_ref(&failing), &other).await;
    assert!(!other.exists());
}

fn plain(server: &MockServer, kind: TargetKind, params: serde_json::Value) -> Target {
    Target {
        kind,
        base_url: Url::parse(&server.uri()).unwrap(),
        timeout_s: 5.0,
        verify_tls: true,
        auth: None,
        retry: None,
        params,
    }
}

#[tokio::test]
async fn chap_plugin_direct() {
    let server = MockServer::start().await;
    mount(
        &server,
        "/health",
        ok_json(json!({"status": "success", "message": "healthy"})),
    )
    .await;
    mount(
        &server,
        "/system/info",
        ok_json(json!({"chap_core_version": "2.7.0", "revision": "62bae69"})),
    )
    .await;
    let results = run(&plain(&server, TargetKind::Chap, serde_json::Value::Null)).await;
    assert_eq!(
        statuses(&results),
        [
            ("chap_health", Status::Ok),
            ("chap_system_info", Status::Ok)
        ]
    );
    assert_eq!(results[0].message, "chap-core healthy (status 200).");
    assert_eq!(results[1].message, "chap-core 2.7.0 / rev 62bae69.");

    server.reset().await;
    mount(
        &server,
        "/health",
        ok_json(json!({"status": "error", "message": "db down"})),
    )
    .await;
    let results = run(&plain(&server, TargetKind::Chap, serde_json::Value::Null)).await;
    assert_eq!(
        results[0].message,
        "chap-core reported health status 'error': db down"
    );
    assert_eq!(results[1].status, Status::Skipped);
}

#[tokio::test]
async fn caps_plugin_reads_its_links() {
    let server = MockServer::start().await;
    mount(
        &server,
        "/system/info",
        ok_json(json!({"caps": {
            "version": "3.2.2",
            "chap": {"connected": true, "info": {"chap_core_version": "2.8.0.dev0"}},
            "dhis": {"connected": false, "info": {"version": "2.42.6"}},
        }})),
    )
    .await;
    let results = run(&plain(&server, TargetKind::Caps, serde_json::Value::Null)).await;
    assert_eq!(
        statuses(&results),
        [
            ("caps_system_info", Status::Ok),
            ("caps_chap", Status::Ok),
            ("caps_dhis2", Status::Fail)
        ]
    );
    assert_eq!(results[0].message, "CAPS 3.2.2.");
    assert_eq!(results[1].message, "chap-core 2.8.0.dev0 connected.");
    assert_eq!(results[2].message, "CAPS reports DHIS2 not connected.");

    server.reset().await;
    mount(&server, "/system/info", ok_json(json!({"status": "ok"}))).await;
    let results = run(&plain(&server, TargetKind::Caps, serde_json::Value::Null)).await;
    assert_eq!(results[0].status, Status::Fail);
    assert_eq!(results[1].status, Status::Skipped);
}

#[tokio::test]
async fn http_plugin_expectations() {
    let server = MockServer::start().await;
    mount(
        &server,
        "/",
        ok_json(json!({"status": "success", "data": [{"state": "up"}]})),
    )
    .await;
    let t = |params| plain(&server, TargetKind::Http, params);
    let one = |results: Vec<CheckResult>| results.into_iter().next().unwrap();

    let r = one(run(&t(serde_json::Value::Null)).await);
    assert_eq!(r.status, Status::Ok);
    assert!(r.message.starts_with("200 from "), "{}", r.message);

    let r = one(run(&t(
        json!({"json_path": "data.0.state", "json_equals": "up", "body_contains": "success"}),
    ))
    .await);
    assert_eq!(r.status, Status::Ok, "{}", r.message);
    assert!(
        r.message.ends_with("; body matches, data.0.state = up"),
        "{}",
        r.message
    );

    let r = one(run(&t(
        json!({"json_path": "status", "json_equals": "degraded"}),
    ))
    .await);
    assert_eq!(
        (r.status, r.message.as_str()),
        (Status::Fail, "'status' is success - expected degraded.")
    );

    let r = one(run(&t(json!({"expect_status": [401]}))).await);
    assert_eq!(r.status, Status::Fail);
    assert!(r.message.contains("(expected 401)"), "{}", r.message);

    server.reset().await;
    mount(&server, "/", ResponseTemplate::new(401)).await;
    let r = one(run(&t(json!({"expect_status": [401]}))).await);
    assert_eq!(r.status, Status::Ok, "a protected URL can count as up");
}

#[tokio::test]
async fn http_headers_never_follow_a_redirect_to_another_origin() {
    let elsewhere = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200))
        .expect(0)
        .mount(&elsewhere)
        .await;
    let server = MockServer::start().await;
    mount(
        &server,
        "/",
        ResponseTemplate::new(302)
            .insert_header("Location", format!("{}/landing", elsewhere.uri())),
    )
    .await;
    let with_header = plain(
        &server,
        TargetKind::Http,
        json!({"headers": {"X-Api-Key": "secret"}}),
    );
    let r = run(&with_header).await.into_iter().next().unwrap();
    assert_eq!(r.status, Status::Fail, "{}", r.message);
    assert!(
        r.message.starts_with("Unexpected status 302"),
        "{}",
        r.message
    );
    elsewhere.verify().await;

    // Without headers there is nothing to leak, so the redirect is followed as before.
    let elsewhere2 = MockServer::start().await;
    mount(&elsewhere2, "/landing", ResponseTemplate::new(200)).await;
    server.reset().await;
    mount(
        &server,
        "/",
        ResponseTemplate::new(302)
            .insert_header("Location", format!("{}/landing", elsewhere2.uri())),
    )
    .await;
    let r = run(&plain(&server, TargetKind::Http, serde_json::Value::Null))
        .await
        .into_iter()
        .next()
        .unwrap();
    assert_eq!(r.status, Status::Ok, "{}", r.message);
}
