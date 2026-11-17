//! `chap-checker serve`: the check loop as a daemon, its JSON API, and the browser dashboard.
//!
//! - `GET /api/state` returns the snapshot (the shape `tui --connect` and the browser read).
//! - `POST /api/refresh` runs a cycle now; `POST /api/reload` re-reads the config.
//! - `GET /api/auth` says whether a token is needed, and the `[ui]` look, unauthenticated so the
//!   login modal can show before a token exists.
//! - `GET /` and the dashboard's static files, built into the binary (`--no-ui` drops them).
//!
//! With `[auth]`, the three data routes need `Authorization: Bearer <token>`, compared in
//! constant time; the token is re-read on every request so a reload can add, rotate or remove it.
//! Responses match the Python daemon's, status codes and JSON bodies included.

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use axum::Router;
use axum::body::Body;
use axum::extract::{ConnectInfo, Request, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use chrono::{SecondsFormat, Timelike};
use include_dir::{Dir, include_dir};
use serde_json::json;

use crate::daemon::{Daemon, ReloadError};

/// The browser dashboard: the same files the Python package ships.
static WEB_UI: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/web_ui");

/// Build the HTTP app around `daemon`.
pub fn app(daemon: Arc<Daemon>, ui_enabled: bool) -> Router {
    let api = Router::new()
        .route("/api/state", get(api_state))
        .route("/api/refresh", post(api_refresh))
        .route("/api/reload", post(api_reload))
        .route_layer(middleware::from_fn_with_state(
            Arc::clone(&daemon),
            require_auth,
        ))
        .route("/api/auth", get(api_auth));
    let mut router = api;
    if ui_enabled {
        // Like Starlette's mount at `/`: any GET that no route takes, including a GET on a
        // POST-only route, is looked up as a file (and is a 404 when there is none).
        router = router
            .route("/", get(index))
            .fallback(static_file)
            .method_not_allowed_fallback(static_file);
    } else {
        router = router
            .fallback(not_found)
            .method_not_allowed_fallback(method_not_allowed);
    }
    router
        .layer(middleware::from_fn(access_log))
        .with_state(daemon)
}

fn detail(status: StatusCode, text: &str) -> Response {
    (status, axum::Json(json!({ "detail": text }))).into_response()
}

async fn not_found() -> Response {
    detail(StatusCode::NOT_FOUND, "Not Found")
}

async fn method_not_allowed() -> Response {
    detail(StatusCode::METHOD_NOT_ALLOWED, "Method Not Allowed")
}

/// Gate a route on the current bearer token; a no-op while auth is off.
async fn require_auth(State(daemon): State<Arc<Daemon>>, request: Request, next: Next) -> Response {
    let Some(token) = daemon.auth_token() else {
        return next.run(request).await;
    };
    let unauthorized = |why: &str| {
        let mut response = detail(StatusCode::UNAUTHORIZED, why);
        response
            .headers_mut()
            .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
        response
    };
    let Some(value) = request.headers().get(header::AUTHORIZATION) else {
        return unauthorized("missing bearer token");
    };
    let Some(provided) = value.as_bytes().strip_prefix(b"Bearer ") else {
        return unauthorized("missing bearer token");
    };
    let provided = provided.trim_ascii();
    if provided.is_empty() || !constant_time_eq(provided, token.as_bytes()) {
        return unauthorized("bad token");
    }
    next.run(request).await
}

/// Compare without stopping at the first difference, so timing says nothing about the token.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

async fn api_state(State(daemon): State<Arc<Daemon>>) -> Response {
    axum::Json(daemon.snapshot()).into_response()
}

async fn api_refresh(State(daemon): State<Arc<Daemon>>) -> Response {
    daemon.refresh_once().await;
    axum::Json(json!({
        "status": "ok",
        "instance_count": daemon.instance_count(),
        "last_refresh": daemon.last_refresh().map(|t| {
            // Python's `isoformat()`: `+00:00`, microseconds only when non-zero.
            let format = if t.nanosecond() / 1000 == 0 { SecondsFormat::Secs } else { SecondsFormat::Micros };
            t.to_rfc3339_opts(format, false)
        }),
    }))
    .into_response()
}

async fn api_reload(State(daemon): State<Arc<Daemon>>) -> Response {
    match daemon.reload() {
        Ok(r) => axum::Json(json!({
            "status": "ok",
            "instance_count": r.instance_count,
            "added": r.added,
            "removed": r.removed,
        }))
        .into_response(),
        Err(ReloadError::NotFound(message)) => (
            StatusCode::NOT_FOUND,
            axum::Json(json!({"status": "error", "message": message})),
        )
            .into_response(),
        Err(ReloadError::Invalid(message)) => {
            tracing::warn!("config reload failed: {message}");
            (
                StatusCode::BAD_REQUEST,
                axum::Json(json!({"status": "error", "message": message})),
            )
                .into_response()
        }
    }
}

async fn api_auth(State(daemon): State<Arc<Daemon>>) -> Response {
    let (title, theme) = daemon.ui();
    axum::Json(json!({
        "required": daemon.auth_token().is_some(),
        "ui_theme": theme,
        "ui_title": title,
    }))
    .into_response()
}

async fn index(method: Method) -> Response {
    serve_file("index.html", &method)
}

async fn static_file(method: Method, request: Request) -> Response {
    if method != Method::GET && method != Method::HEAD {
        return method_not_allowed().await;
    }
    let path = request.uri().path().trim_start_matches('/');
    serve_file(path, &method)
}

fn serve_file(path: &str, method: &Method) -> Response {
    let Some(file) = (!path.is_empty() && !path.split('/').any(|p| p == ".." || p.is_empty()))
        .then(|| WEB_UI.get_file(path))
        .flatten()
    else {
        return detail(StatusCode::NOT_FOUND, "Not Found");
    };
    let mut headers = HeaderMap::new();
    headers.insert(header::CONTENT_TYPE, content_type(path));
    headers.insert(
        header::CONTENT_LENGTH,
        HeaderValue::from(file.contents().len()),
    );
    let body = if method == Method::HEAD {
        Body::empty()
    } else {
        Body::from(file.contents())
    };
    (StatusCode::OK, headers, body).into_response()
}

/// The content type Starlette's file response sends: Python's guess, `text/*` with a UTF-8
/// charset.
fn content_type(path: &str) -> HeaderValue {
    let extension = Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("");
    let mime = match extension {
        "js" => "text/javascript".to_owned(),
        // Python's `mimetypes` doesn't know these; Starlette then sends octet-stream.
        "jsx" | "map" | "" => "application/octet-stream".to_owned(),
        other => mime_guess::from_ext(other)
            .first_or_text_plain()
            .essence_str()
            .to_owned(),
    };
    let value = if mime.starts_with("text/") {
        format!("{mime}; charset=utf-8")
    } else {
        mime
    };
    HeaderValue::from_str(&value).unwrap_or(HeaderValue::from_static("application/octet-stream"))
}

/// One line per request: client, request line, status, size, duration, user agent.
async fn access_log(request: Request, next: Next) -> Response {
    let started = Instant::now();
    let client = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map_or_else(|| "-".to_owned(), |c| c.0.ip().to_string());
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let version = format!("{:?}", request.version());
    let ua = request
        .headers()
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("-")
        .to_owned();
    let response = next.run(request).await;
    let size = response
        .headers()
        .get(header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
        .or_else(|| {
            axum::body::HttpBody::size_hint(response.body())
                .exact()
                .map(|n| n.to_string())
        })
        .unwrap_or_else(|| "-".to_owned());
    tracing::info!(
        target: "chap_checker::serve::access",
        "{client} \"{method} {path} {version}\" {} {size} {:.1}ms ua=\"{ua}\"",
        response.status().as_u16(),
        started.elapsed().as_secs_f64() * 1000.0
    );
    response
}

/// Options for [`run`].
pub struct ServeOptions {
    pub host: String,
    pub port: u16,
    pub ui_enabled: bool,
}

/// Run the check loop and serve until Ctrl+C or SIGTERM.
pub async fn run(daemon: Arc<Daemon>, options: ServeOptions) -> std::io::Result<()> {
    let surface = if options.ui_enabled {
        "dashboard + API"
    } else {
        "API only (--no-ui)"
    };
    let address = format!("{}:{}", options.host, options.port);
    let listener = tokio::net::TcpListener::bind(&address).await?;
    tracing::info!("serving {surface} on http://{address}");
    if let Some(path) = &daemon.config_path {
        tracing::info!("config: {}", path.display());
    }
    let auth_on = daemon.auth_token().is_some();
    tracing::info!(
        "interval: {:.1}s; alerts: {}; auth: {}",
        daemon.interval_s,
        if daemon.alerts_enabled { "on" } else { "off" },
        if auth_on { "bearer-token" } else { "off" }
    );
    // Reachable beyond loopback with no token: not fatal (a proxy or VPN may front it), but loud.
    if !auth_on && !matches!(options.host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        tracing::warn!(
            "serving on {} without [auth] - the daemon is reachable from outside loopback and /api/state is \
             unauthenticated. Add an [auth] block to chap-checker.toml (see `chap-checker.toml.example`) or \
             front the daemon with a reverse proxy.",
            options.host
        );
    }
    let refresher = tokio::spawn(Arc::clone(&daemon).run_loop());
    let router = app(daemon, options.ui_enabled);
    let result = axum::serve(
        listener,
        router.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown())
    .await;
    refresher.abort();
    result
}

async fn shutdown() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! {
        () = ctrl_c => {},
        () = terminate => {},
    }
    tracing::info!("shutting down");
}
