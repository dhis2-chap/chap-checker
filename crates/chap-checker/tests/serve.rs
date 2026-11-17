//! The HTTP surface of `serve`, driven in-process against a mock OGS server.

use std::path::Path;
use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use chap_checker::config::load_config;
use chap_checker::daemon::Daemon;
use chap_checker::serve::app;
use http_body_util::BodyExt;
use serde_json::{Value, json};
use tower::ServiceExt;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const TOKEN: &str = "serve-test-token-123";

async fn ogs() -> MockServer {
    let server = MockServer::start().await;
    for (at, body) in [
        ("/", json!({"api_version": "1.2.0"})),
        ("/health", json!({"status": "healthy"})),
        ("/info", json!({"app_version": "0.4.1"})),
    ] {
        Mock::given(method("GET"))
            .and(path(at))
            .respond_with(ResponseTemplate::new(200).set_body_json(body))
            .mount(&server)
            .await;
    }
    server
}

fn write_config(path: &Path, url: &str, extra: &str) {
    std::fs::write(
        path,
        format!("{extra}\n[ui]\ntitle = \"Test\"\ntheme = \"tokyo\"\n\n[instances.nepal]\nkind = \"ogs\"\nname = \"Nepal\"\nurl = \"{url}\"\n"),
    )
    .unwrap();
}

fn daemon(config: &Path) -> Arc<Daemon> {
    let cfg = load_config(config).unwrap();
    let targets = cfg.target_entries().unwrap();
    let token = cfg.auth.as_ref().map(|a| a.resolve_token().unwrap());
    Arc::new(Daemon::new(
        targets,
        cfg,
        token,
        30.0,
        false,
        None,
        Some(config.to_path_buf()),
    ))
}

async fn call(
    router: &axum::Router,
    method: &str,
    uri: &str,
    token: Option<&str>,
) -> (StatusCode, Vec<u8>, axum::http::HeaderMap) {
    let mut request = Request::builder().method(method).uri(uri);
    if let Some(t) = token {
        request = request.header(header::AUTHORIZATION, format!("Bearer {t}"));
    }
    let response = router
        .clone()
        .oneshot(request.body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = response
        .into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec();
    (status, body, headers)
}

fn json_of(body: &[u8]) -> Value {
    serde_json::from_slice(body).unwrap()
}

#[tokio::test]
async fn state_refresh_and_auth() {
    let server = ogs().await;
    let dir = tempfile::tempdir().unwrap();
    let config = dir.path().join("chap-checker.toml");
    write_config(
        &config,
        &server.uri(),
        &format!("[auth]\ntoken = \"{TOKEN}\""),
    );
    let d = daemon(&config);
    let router = app(Arc::clone(&d), true);

    let (status, body, _) = call(&router, "GET", "/api/auth", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        json_of(&body),
        json!({"required": true, "ui_theme": "tokyo", "ui_title": "Test"})
    );

    let (status, body, headers) = call(&router, "GET", "/api/state", None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(json_of(&body), json!({"detail": "missing bearer token"}));
    assert_eq!(headers[header::WWW_AUTHENTICATE], "Bearer");
    let (status, body, _) = call(&router, "GET", "/api/state", Some("wrong")).await;
    assert_eq!(
        (status, json_of(&body)),
        (StatusCode::UNAUTHORIZED, json!({"detail": "bad token"}))
    );

    let (_, body, _) = call(&router, "GET", "/api/state", Some(TOKEN)).await;
    let state = json_of(&body);
    assert_eq!(state["last_refresh"], Value::Null);
    assert_eq!(state["tiles"][0]["worst_status"], "skipped");

    let (status, body, _) = call(&router, "POST", "/api/refresh", Some(TOKEN)).await;
    assert_eq!(status, StatusCode::OK);
    let refreshed = json_of(&body);
    assert_eq!(refreshed["status"], "ok");
    assert!(
        refreshed["last_refresh"]
            .as_str()
            .unwrap()
            .ends_with("+00:00")
    );

    let (_, body, _) = call(&router, "GET", "/api/state", Some(TOKEN)).await;
    let tile = &json_of(&body)["tiles"][0];
    assert_eq!(tile["display_name"], "Nepal");
    assert_eq!(tile["platform"], "OGS");
    assert_eq!(tile["version"], "0.4.1");
    assert_eq!(tile["worst_status"], "ok");
    assert_eq!(
        (tile["ping_ok"].as_u64(), tile["ping_total"].as_u64()),
        (Some(1), Some(1))
    );
    assert_eq!(tile["uptime_pct"], 100.0);
    assert_eq!(
        tile["checks"][1],
        json!({"name": "health", "status": "ok", "symbol": "\u{2713}", "message": "Service healthy."})
    );
    assert!(tile["last_refresh"].as_str().unwrap().ends_with('Z'));
}

#[tokio::test]
async fn reload_adds_rotates_and_removes_auth() {
    let server = ogs().await;
    let dir = tempfile::tempdir().unwrap();
    let config = dir.path().join("chap-checker.toml");
    write_config(&config, &server.uri(), "");
    let d = daemon(&config);
    let router = app(Arc::clone(&d), true);
    assert_eq!(
        call(&router, "GET", "/api/state", None).await.0,
        StatusCode::OK
    );

    write_config(
        &config,
        &server.uri(),
        &format!("[auth]\ntoken = \"{TOKEN}\""),
    );
    let (status, body, _) = call(&router, "POST", "/api/reload", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        json_of(&body),
        json!({"status": "ok", "instance_count": 1, "added": [], "removed": []})
    );
    assert_eq!(
        call(&router, "GET", "/api/state", None).await.0,
        StatusCode::UNAUTHORIZED
    );

    // A broken config is a 400 that leaves the running config (and its token) in place.
    std::fs::write(
        &config,
        "[instances.a]\nurl = \"https://x\"\npassword = 123456\n",
    )
    .unwrap();
    let (status, body, _) = call(&router, "POST", "/api/reload", Some(TOKEN)).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let message = json_of(&body)["message"].as_str().unwrap().to_owned();
    assert!(
        message.contains("instances.a.dhis2.password: Input should be a valid string"),
        "{message}"
    );
    assert!(!message.contains("123456"));
    assert_eq!(
        call(&router, "GET", "/api/state", Some(TOKEN)).await.0,
        StatusCode::OK
    );

    std::fs::remove_file(&config).unwrap();
    let (status, body, _) = call(&router, "POST", "/api/reload", Some(TOKEN)).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(
        json_of(&body)["message"]
            .as_str()
            .unwrap()
            .starts_with("[Errno 2] No such file or directory: '")
    );

    std::fs::write(
        &config,
        format!(
            "[instances.other]\nkind = \"ogs\"\nurl = \"{}\"\n",
            server.uri()
        ),
    )
    .unwrap();
    let (_, body, _) = call(&router, "POST", "/api/reload", Some(TOKEN)).await;
    assert_eq!(
        json_of(&body),
        json!({"status": "ok", "instance_count": 1, "added": ["other"], "removed": ["nepal"]})
    );
    assert_eq!(
        call(&router, "GET", "/api/state", None).await.0,
        StatusCode::OK,
        "auth removed with its block"
    );
}

#[tokio::test]
async fn static_files_and_fallbacks() {
    let server = ogs().await;
    let dir = tempfile::tempdir().unwrap();
    let config = dir.path().join("chap-checker.toml");
    write_config(&config, &server.uri(), "");
    let d = daemon(&config);
    let router = app(Arc::clone(&d), true);

    let (status, body, headers) = call(&router, "GET", "/", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers[header::CONTENT_TYPE], "text/html; charset=utf-8");
    assert!(String::from_utf8_lossy(&body).contains("<html"));
    let (_, _, headers) = call(&router, "GET", "/_state.js", None).await;
    assert_eq!(
        headers[header::CONTENT_TYPE],
        "text/javascript; charset=utf-8"
    );
    let (_, _, headers) = call(&router, "GET", "/src/app.jsx", None).await;
    assert_eq!(headers[header::CONTENT_TYPE], "application/octet-stream");

    for uri in ["/nope", "/src/", "/../Cargo.toml", "/api/reload"] {
        let (status, body, _) = call(&router, "GET", uri, None).await;
        assert_eq!(
            (status, json_of(&body)),
            (StatusCode::NOT_FOUND, json!({"detail": "Not Found"})),
            "{uri}"
        );
    }
    let (status, body, _) = call(&router, "POST", "/api/state", None).await;
    assert_eq!(
        (status, json_of(&body)),
        (
            StatusCode::METHOD_NOT_ALLOWED,
            json!({"detail": "Method Not Allowed"})
        )
    );

    let headless = app(d, false);
    assert_eq!(
        call(&headless, "GET", "/", None).await.0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&headless, "GET", "/api/reload", None).await.0,
        StatusCode::METHOD_NOT_ALLOWED
    );
    assert_eq!(
        call(&headless, "GET", "/api/state", None).await.0,
        StatusCode::OK
    );
}
