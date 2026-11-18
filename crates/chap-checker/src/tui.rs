//! `chap-checker tui`: the dashboard in a terminal, drawn with sokkel-tui.
//!
//! The app is a [`ShellApp`] in sokkel's frame. The rail lists the instances, each with its
//! worst status. The overview page leads with four summary tiles, then a table of the instances.
//! Choosing an instance (the rail, its row, the palette) opens its page: its checks, its uptime
//! and its latency over the last refreshes. A check opened there (or found by the palette's
//! search) shows in full in the side panel.
//!
//! The data comes from a background task on a tokio runtime, in one of two modes:
//!
//! - **Local**: the [`Daemon`] runs the checks in-process, as `serve` does.
//! - **Connect** (`--connect URL`): polls a `chap-checker serve` daemon's `/api/state`. A failed
//!   poll keeps the last data on screen, marked stale under a banner. A 401 opens a token prompt
//!   once; after that, a rejected token is a banner.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{DateTime, Local, Utc};
use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::widgets::Widget;
use sokkel_tui::app::{self, Chrome, Cx, Flow, Ran, SearchAnswer, ShellApp};
use sokkel_tui::cells::Width;
use sokkel_tui::dashboard::grid;
use sokkel_tui::{
    BannerTone, Binding, Button, ButtonVariant, Cell, Charset, Column, ColumnWidth, Command,
    CommandGroup, CommandTone, Cut, DashboardPanel, Depth, DetailPane, Fact, FocusId, Key,
    KeyPress, Keys, Look, NoteTone, PageHeader, PaneSection, PanelRead, Pointer, PointerKind,
    Prompt, PromptEvent, PromptState, Rail, RailEntry, RailGroup, Row, RowTone, Scheme, Span,
    StatPanel, StatusLine, StatusTone, Table, TableState, Threshold, TopBar, snapshot,
};
use tokio::sync::mpsc;

use crate::checks::Status;
use crate::config::UiTheme;
use crate::daemon::{CheckRow, Daemon, DashboardState, Tile};

/// Where the dashboard's data comes from.
pub enum Source {
    Local(Arc<Daemon>),
    Connect { url: String, token: Option<String> },
}

/// What the app asks of the background task.
#[derive(Debug, Clone)]
enum Ask {
    Refresh,
    Reload,
    Token(String),
    Cancel,
}

/// How the connection to the data stands.
#[derive(Debug, Clone, PartialEq)]
enum Conn {
    /// No answer yet.
    Waiting,
    Live,
    Disconnected(String),
    /// A 401 with no token: the prompt opens (once).
    NeedsToken,
    Rejected(String),
}

/// What the background task shares with the app.
struct Shared {
    state: Option<DashboardState>,
    conn: Conn,
    busy: bool,
    /// A passing word (a reload's outcome), until the given time.
    notice: Option<(BannerTone, String, DateTime<Utc>)>,
    last_contact: Option<DateTime<Utc>>,
}

type SharedRef = Arc<Mutex<Shared>>;

fn lock(shared: &SharedRef) -> std::sync::MutexGuard<'_, Shared> {
    shared.lock().unwrap_or_else(|e| e.into_inner())
}

fn notice(shared: &SharedRef, tone: BannerTone, words: String) {
    lock(shared).notice = Some((tone, words, Utc::now() + chrono::Duration::seconds(8)));
}

/// Local mode: run the daemon's loop, mirror its snapshot, and answer the app's asks.
async fn local(daemon: Arc<Daemon>, shared: SharedRef, mut asks: mpsc::UnboundedReceiver<Ask>) {
    tokio::spawn(Arc::clone(&daemon).run_loop());
    let mut tick = tokio::time::interval(Duration::from_millis(250));
    loop {
        tokio::select! {
            _ = tick.tick() => {
                let snapshot = daemon.snapshot();
                let mut s = lock(&shared);
                if snapshot.last_refresh.is_some() {
                    s.conn = Conn::Live;
                    s.last_contact = snapshot.last_refresh;
                }
                s.state = Some(snapshot);
            }
            ask = asks.recv() => match ask {
                None => return,
                Some(Ask::Refresh) => {
                    lock(&shared).busy = true;
                    let (d, s) = (Arc::clone(&daemon), Arc::clone(&shared));
                    tokio::spawn(async move {
                        d.refresh_once().await;
                        lock(&s).busy = false;
                    });
                }
                Some(Ask::Reload) => match daemon.reload() {
                    Ok(r) => notice(&shared, BannerTone::Good, reloaded(r.instance_count, &r.added, &r.removed)),
                    Err(crate::daemon::ReloadError::NotFound(m) | crate::daemon::ReloadError::Invalid(m)) => {
                        notice(&shared, BannerTone::Critical, format!("reload failed: {}", one_line(&m)));
                    }
                },
                Some(Ask::Token(_) | Ask::Cancel) => {}
            }
        }
    }
}

fn reloaded(count: usize, added: &[String], removed: &[String]) -> String {
    let mut words = format!("config reloaded: {count} instance(s)");
    if !added.is_empty() {
        words.push_str(&format!("; added {}", added.join(", ")));
    }
    if !removed.is_empty() {
        words.push_str(&format!("; removed {}", removed.join(", ")));
    }
    words
}

fn one_line(text: &str) -> String {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Connect mode: poll `/api/state` every interval, and answer the app's asks.
async fn connect(
    url: String,
    mut token: Option<String>,
    interval: f64,
    shared: SharedRef,
    mut asks: mpsc::UnboundedReceiver<Ask>,
) {
    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent(concat!("chap-checker/", env!("CARGO_PKG_VERSION")))
        .build()
        .expect("an HTTP client");
    let base = url.trim_end_matches('/').to_owned();
    // The prompt opens on the first 401 only when no token was given.
    let mut prompted = token.is_some();
    loop {
        poll(&http, &base, &url, token.as_deref(), prompted, &shared).await;
        let ask = tokio::select! {
            () = tokio::time::sleep(crate::http::seconds(interval)) => None,
            ask = asks.recv() => match ask { None => return, a => a },
        };
        let send = |method: reqwest::Method, path: &str| {
            let mut request = http.request(method, format!("{base}{path}"));
            if let Some(t) = &token {
                request = request.bearer_auth(t);
            }
            request
        };
        match ask {
            None => {}
            Some(Ask::Refresh) => {
                lock(&shared).busy = true;
                let outcome = send(reqwest::Method::POST, "/api/refresh")
                    .timeout(Duration::from_secs(300))
                    .send()
                    .await;
                lock(&shared).busy = false;
                // A 401 is the poll's to report (prompt or banner); anything else failing says so.
                match outcome {
                    Ok(r)
                        if r.status().is_success()
                            || r.status() == reqwest::StatusCode::UNAUTHORIZED => {}
                    Ok(r) => notice(
                        &shared,
                        BannerTone::Critical,
                        format!("refresh failed: status {}", r.status().as_u16()),
                    ),
                    Err(e) => notice(
                        &shared,
                        BannerTone::Critical,
                        format!(
                            "refresh failed: {}",
                            crate::http::RequestError::from_reqwest(&e).describe(None)
                        ),
                    ),
                }
            }
            Some(Ask::Reload) => match send(reqwest::Method::POST, "/api/reload").send().await {
                Ok(r) => {
                    let ok = r.status().is_success();
                    let body: serde_json::Value = r.json().await.unwrap_or_default();
                    if ok {
                        let names = |k: &str| -> Vec<String> {
                            body[k]
                                .as_array()
                                .map(|a| {
                                    a.iter()
                                        .filter_map(|v| v.as_str().map(str::to_owned))
                                        .collect()
                                })
                                .unwrap_or_default()
                        };
                        let count = body["instance_count"].as_u64().unwrap_or(0) as usize;
                        notice(
                            &shared,
                            BannerTone::Good,
                            reloaded(count, &names("added"), &names("removed")),
                        );
                    } else {
                        let why = body["message"]
                            .as_str()
                            .or(body["detail"].as_str())
                            .unwrap_or("no reason given");
                        notice(
                            &shared,
                            BannerTone::Critical,
                            format!("reload failed: {}", one_line(why)),
                        );
                    }
                }
                Err(e) => notice(&shared, BannerTone::Critical, format!("reload failed: {e}")),
            },
            Some(Ask::Token(t)) => {
                token = Some(t);
                prompted = true;
            }
            Some(Ask::Cancel) => {
                prompted = true;
                lock(&shared).conn = Conn::Rejected(format!(
                    "auth rejected by {url}: set --token-env or sign in"
                ));
                continue;
            }
        }
    }
}

async fn poll(
    http: &reqwest::Client,
    base: &str,
    url: &str,
    token: Option<&str>,
    prompted: bool,
    shared: &SharedRef,
) {
    let mut request = http.get(format!("{base}/api/state"));
    if let Some(t) = token {
        request = request.bearer_auth(t);
    }
    let outcome = match request.send().await {
        Err(e) => Err(crate::http::RequestError::from_reqwest(&e).describe(None)),
        Ok(r) if r.status() == reqwest::StatusCode::UNAUTHORIZED => {
            let conn = match (token, prompted) {
                (None, false) => Conn::NeedsToken,
                (None, true) => Conn::Rejected(format!("auth rejected by {url}: set --token-env")),
                (Some(_), _) => {
                    Conn::Rejected(format!("auth rejected by {url}: check the token value"))
                }
            };
            lock(shared).conn = conn;
            return;
        }
        Ok(r) if !r.status().is_success() => Err(format!("status {}", r.status().as_u16())),
        Ok(r) => r
            .json::<DashboardState>()
            .await
            .map_err(|e| format!("unreadable state: {e}")),
    };
    let mut s = lock(shared);
    match outcome {
        Ok(state) => {
            s.state = Some(state);
            s.conn = Conn::Live;
            s.last_contact = Some(Utc::now());
        }
        Err(why) => s.conn = Conn::Disconnected(format!("disconnected from {url}: {why}")),
    }
}

/// A check status in sokkel's tones: the operator's words, the contract's colors.
fn tone(status: Status) -> StatusTone {
    match status {
        Status::Ok => StatusTone::Good,
        Status::Warn => StatusTone::Warning,
        Status::Fail | Status::Error => StatusTone::Critical,
        Status::Skipped => StatusTone::Quiet,
    }
}

fn row_tone(status: Status) -> RowTone {
    match status {
        Status::Fail | Status::Error => RowTone::Critical,
        Status::Warn => RowTone::Warning,
        Status::Skipped => RowTone::Muted,
        Status::Ok => RowTone::Plain,
    }
}

/// The ping uptime's scale: critical below 95 %, warning below 99 %.
fn uptime_scale() -> Vec<Threshold> {
    vec![
        Threshold::new(None, "critical"),
        Threshold::new(Some(95.0), "warning"),
        Threshold::new(Some(99.0), "good"),
    ]
}

fn clock(t: Option<DateTime<Utc>>) -> String {
    t.map(|t| t.with_timezone(&Local).format("%H:%M:%S").to_string())
        .unwrap_or_else(|| "not yet".into())
}

fn label(tile: &Tile) -> &str {
    tile.display_name.as_deref().unwrap_or(&tile.name)
}

/// "DHIS2 2.42.6", or the platform alone.
fn platform(tile: &Tile) -> String {
    match &tile.version {
        Some(v) => format!("{} {v}", tile.platform),
        None => tile.platform.clone(),
    }
}

/// The page's focus stops: the two tables pass Left and Right on to back and forward when they
/// don't use them; the side panel's verbs keep theirs.
const INSTANCES: &str = "instances";
const CHECKS: &str = "checks";
const PANE_VERBS: [&str; 2] = ["pane-refresh", "pane-close"];
/// The instances panel's Retry, while it reads failed (a daemon that never answered).
const RETRY: &str = "retry";

/// See [`App::page_width`].
const PAGE_WIDTH: u16 = 77;

/// An instance's health for the summaries, from its worst check: a failure or error is down, a
/// warning is up but flagged, and one that hasn't answered yet is waiting.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Health {
    Up,
    Waiting,
    Warning,
    Down,
}

fn health(tile: &Tile) -> Health {
    if tile.total_count == 0 {
        return Health::Waiting;
    }
    match tile.worst_status {
        Status::Fail | Status::Error => Health::Down,
        Status::Warn => Health::Warning,
        _ => Health::Up,
    }
}

/// How many instances are down, have warnings, and are up (warnings included).
fn fleet(tiles: &[Tile]) -> (usize, usize, usize) {
    let count = |h| tiles.iter().filter(|t| health(t) == h).count();
    let (down, warning) = (count(Health::Down), count(Health::Warning));
    (down, warning, count(Health::Up) + warning)
}

/// The note under a count: what is wrong, or that nothing is.
fn fleet_note(down: usize, warning: usize) -> String {
    match (down, warning) {
        (0, 0) => "all up".to_owned(),
        (d, 0) => format!("{d} down"),
        (0, w) => format!("{w} with warnings"),
        (d, w) => format!("{d} down, {w} with warnings"),
    }
}

/// The token a tile's value takes: critical while anything is down, warning while anything warns.
fn fleet_color(down: usize, warning: usize) -> &'static str {
    match (down, warning) {
        (0, 0) => "good",
        (0, _) => "warning",
        _ => "critical",
    }
}

/// A check status as sokkel says it: the contract's health map gives ok, warn, fail and error
/// their tones; skipped is quiet.
fn state_of(status: Status) -> sokkel_tui::Status {
    match status {
        Status::Skipped => sokkel_tui::Status::toned("skipped", StatusTone::Quiet),
        s => sokkel_tui::Status::of(s.as_str()),
    }
}

/// An instance's status for its header: its worst check's, or waiting before its first answer.
fn tile_state(tile: &Tile) -> sokkel_tui::Status {
    if tile.total_count == 0 {
        sokkel_tui::Status::toned("waiting", StatusTone::Quiet)
    } else {
        state_of(tile.worst_status)
    }
}

/// The instances table's first order: the worst health first, then as configured.
fn overview_order() -> TableState {
    TableState::default().sorted_by(2, sokkel_tui::SortDir::Descending)
}

#[derive(Debug, Clone, PartialEq)]
enum Route {
    Overview,
    Instance(String),
}

/// The app sokkel's runtime drives.
pub struct App {
    shared: SharedRef,
    asks: mpsc::UnboundedSender<Ask>,
    source: String,
    interval: f64,
    kiosk: bool,
    route: Route,
    state: Option<DashboardState>,
    conn: Conn,
    busy: bool,
    notice: Option<(BannerTone, String)>,
    last_contact: Option<DateTime<Utc>>,
    checks: TableState,
    prompt: Option<PromptState>,
    /// The token prompt's round with the daemon: since when (ms) it has been checking the token,
    /// and why the daemon turned the last one down.
    prompt_busy: Option<u64>,
    prompt_failed: Option<String>,
    now: u64,
    prompt_at: Rect,
    /// The instances table's state, and where it stood, for keys and presses.
    instances: TableState,
    table_at: Rect,
    panel_at: Rect,
    /// Where the instance page's checks table and the side panel stood, for presses.
    checks_at: Rect,
    pane_at: Rect,
    /// The palette search's answer to the last query.
    found: Option<SearchAnswer>,
}

impl App {
    fn tiles(&self) -> &[Tile] {
        self.state.as_ref().map_or(&[], |s| s.tiles.as_slice())
    }

    fn tile(&self, name: &str) -> Option<&Tile> {
        self.tiles().iter().find(|t| t.name == name)
    }

    /// "since 12:04" while the data on screen is the last that came, before a lost connection.
    fn stale(&self) -> Option<String> {
        let lost = matches!(self.conn, Conn::Disconnected(_) | Conn::Rejected(_));
        let since = self.last_contact.filter(|_| lost)?;
        Some(format!(
            "since {}",
            since.with_timezone(&Local).format("%H:%M")
        ))
    }

    /// Nothing has answered yet, and something stands in the way.
    fn never_answered(&self) -> bool {
        self.state.is_none() && matches!(self.conn, Conn::Disconnected(_) | Conn::Rejected(_))
    }

    fn token_prompt<'a>(&self, look: &'a Look, window: Rect) -> Prompt<'a> {
        Prompt::new(look, format!("Sign in to {}", self.source), "Bearer token")
            .busy(self.prompt_busy.map(|since| self.now.saturating_sub(since)))
            .failed(self.prompt_failed.clone().map(Some))
            .description("The chap-checker daemon asks for a token ([auth] in its config).")
            .field(|f| {
                f.masked(true)
                    .help("Paste works; Enter signs in, Escape cancels.")
            })
            .submit("Sign in")
            .window(Width::of(window))
    }

    fn prompted(&mut self, event: PromptEvent, state: PromptState) {
        match event {
            // The prompt stays open, busy, until the daemon answers: a sign-in closes it, a
            // refusal says why in it and keeps the token for another try.
            PromptEvent::Submit(token) if !token.trim().is_empty() => {
                let _ = self.asks.send(Ask::Token(token.trim().to_owned()));
                lock(&self.shared).conn = Conn::Waiting;
                self.prompt_busy = Some(self.now);
                self.prompt_failed = None;
                self.prompt = Some(state);
            }
            PromptEvent::Submit(_) | PromptEvent::Cancel => {
                self.prompt_busy = None;
                self.prompt_failed = None;
                let _ = self.asks.send(Ask::Cancel);
            }
            _ => self.prompt = Some(state),
        }
    }

    fn overview_header<'a>(&self, look: &'a Look) -> PageHeader<'a> {
        let title = self
            .state
            .as_ref()
            .map_or("chap-checker", |s| s.ui_title.as_str());
        let n = self.tiles().len();
        let (down, warning, up) = fleet(self.tiles());
        let alerts = self.state.as_ref().is_some_and(|s| s.alerts_enabled);
        if self.state.is_none() {
            let words = if self.never_answered() {
                format!("No answer from {} yet.", self.source)
            } else {
                format!("Waiting for the first answer from {}.", self.source)
            };
            return PageHeader::new(look, title.to_owned()).description(words);
        }
        let lead = match (down, warning) {
            (0, 0) => sokkel_tui::Status::of("ok"),
            (0, _) => sokkel_tui::Status::of("warn"),
            _ => sokkel_tui::Status::of("fail"),
        };
        PageHeader::new(look, title.to_owned())
            .lead_status(lead)
            .description(format!(
                "{up} of {n} instance(s) up{}. Checked every {}s; alerts {}.",
                match (down, warning) {
                    (0, 0) => String::new(),
                    _ => format!(" ({})", fleet_note(down, warning)),
                },
                self.interval,
                if alerts { "on" } else { "off" }
            ))
    }

    fn summary<'a>(&self, look: &'a Look) -> Vec<StatPanel<'a>> {
        let tiles = self.tiles();
        let n = tiles.len();
        let (down, warning, up) = fleet(tiles);
        let checks: usize = tiles.iter().map(|t| t.total_count).sum();
        let ok: usize = tiles.iter().map(|t| t.ok_count).sum();
        let failures: Vec<(&Tile, &CheckRow)> = tiles
            .iter()
            .flat_map(|t| t.checks.iter().map(move |c| (t, c)))
            .filter(|(_, c)| matches!(c.status, Status::Fail | Status::Error))
            .collect();
        let failing = failures.len();
        // The failing tile names the first failure (the worst rows lead the table below), as
        // sokkel's dashboard example does; the value already says how many there are.
        let failing_note = failures
            .first()
            .map(|(t, c)| format!("{} on {}", c.name, label(t)));
        let uptimes: Vec<f64> = tiles.iter().filter_map(|t| t.uptime_pct).collect();
        let uptime =
            (!uptimes.is_empty()).then(|| uptimes.iter().sum::<f64>() / uptimes.len() as f64);
        let stale = self.stale();
        let mark = |p: StatPanel<'a>| match &stale {
            Some(since) => p.stale(since.clone()),
            None => p,
        };
        let waiting =
            !self.never_answered() && self.state.as_ref().is_none_or(|s| s.last_refresh.is_none());
        let none = self.never_answered();
        if none {
            return ["Instances up", "Checks OK", "Failing checks", "Ping uptime"]
                .into_iter()
                .map(|t| StatPanel::new(look, t, None))
                .collect();
        }
        // Before the first refresh: the tiles load, with nothing to note yet.
        if waiting {
            return ["Instances up", "Checks OK", "Failing checks", "Ping uptime"]
                .into_iter()
                .map(|t| StatPanel::new(look, t, None).read(PanelRead::Loading(0)))
                .collect();
        }
        let read = |p: StatPanel<'a>| p;
        vec![
            mark(read(
                StatPanel::new(look, "Instances up", Some(up as f64))
                    .unit(format!("of {n}"))
                    .note(fleet_note(down, warning))
                    .color(fleet_color(down, warning)),
            )),
            mark(read(
                StatPanel::new(look, "Checks OK", Some(ok as f64))
                    .unit(format!("of {checks}"))
                    .note(format!("across {n} instance(s)")),
            )),
            mark(read(
                {
                    let p = StatPanel::new(look, "Failing checks", Some(failing as f64));
                    match failing_note {
                        Some(words) => p.note(words),
                        None => p,
                    }
                }
                .color(if failing == 0 { "good" } else { "critical" })
                .color_mode(if failing == 0 {
                    sokkel_tui::ColorMode::Value
                } else {
                    sokkel_tui::ColorMode::Background
                }),
            )),
            mark(read(
                StatPanel::new(look, "Ping uptime", uptime)
                    .unit("%")
                    .decimals(1)
                    .thresholds(uptime_scale())
                    .note("since start"),
            )),
        ]
    }

    /// The overview's layout: the four tiles, then the instances table.
    fn overview_spans(&self, look: &Look) -> Vec<(Span, u16)> {
        let th = self
            .summary(look)
            .iter()
            .map(StatPanel::height)
            .max()
            .unwrap_or(6);
        let mut spans: Vec<(Span, u16)> = (0..4).map(|_| (Span::Quarter, th)).collect();
        spans.push((Span::Full, self.tiles().len().max(1) as u16 + 6));
        spans
    }

    fn instance_columns() -> Vec<Column> {
        vec![
            Column::new("Instance")
                .width(ColumnWidth::Fill(3))
                .sortable()
                .cut(Cut::Middle),
            Column::new("Platform").width(ColumnWidth::Fill(1)),
            Column::new("Status").sortable(),
            Column::new("Checks"),
            Column::new("Ping"),
            Column::new("Latency")
                .align(sokkel_tui::Align::End)
                .sortable(),
            Column::new("Checked"),
        ]
    }

    fn instance_rows(&self) -> Vec<Row> {
        self.tiles()
            .iter()
            .map(|t| {
                let status = if t.total_count == 0 {
                    Cell::toned("waiting", StatusTone::Quiet)
                } else {
                    Cell::toned(t.worst_status.as_str(), tone(t.worst_status))
                }
                .sort_by(health(t) as u8 as f64);
                let ping = t
                    .uptime_pct
                    .map(|p| format!("{p:.0}% ({}/{})", t.ping_ok, t.ping_total));
                let row = Row::new([
                    Cell::text(label(t)),
                    Cell::text(platform(t)),
                    status,
                    Cell::text(format!("{}/{}", t.ok_count, t.total_count)),
                    Cell::maybe(ping),
                    Cell::maybe(t.latency_ms.map(|ms| format!("{ms} ms")))
                        .sort_by(t.latency_ms.unwrap_or(0) as f64),
                    Cell::text(clock(t.last_refresh)),
                ]);
                if t.total_count == 0 {
                    row.tone(RowTone::Muted)
                } else {
                    row.tone(row_tone(t.worst_status))
                }
            })
            .collect()
    }

    fn instances_panel<'a>(&self, look: &'a Look) -> DashboardPanel<'a> {
        let mut p = DashboardPanel::new(look, "Instances")
            .subtitle(match self.tiles().len() {
                0 => "none yet".to_owned(),
                n => format!("{n} configured; Enter or a click opens one"),
            })
            .flush(true);
        if let (true, Conn::Disconnected(why) | Conn::Rejected(why)) =
            (self.never_answered(), &self.conn)
        {
            p = p.read(PanelRead::Failed(Some(why.clone())));
        } else if self.tiles().is_empty() {
            p = p.read(PanelRead::Empty);
        }
        if let Some(since) = self.stale() {
            p = p.stale(since);
        }
        p
    }

    fn draw_overview(&mut self, f: &mut Frame, page: Rect, cx: &mut Cx) {
        let look = cx.look;
        let header = self.overview_header(look);
        let hh = header.height(page.width);
        f.render_widget(&header, Rect { height: hh, ..page });
        let area = Rect {
            y: page.y + hh + 1,
            height: page.height.saturating_sub(hh + 1),
            ..page
        };
        let rects = grid(look, area, &self.overview_spans(look));
        let summary = self.summary(look);
        for (tile, r) in summary.iter().zip(&rects) {
            tile.render(r.intersection(area), f.buffer_mut());
        }
        if let Some(r) = rects.get(summary.len()) {
            let p = self.instances_panel(look);
            let retry = p
                .layout(*r)
                .retry
                .is_some_and(|at| cx.focus.stop(FocusId::new(RETRY), at));
            let p = p.focus(retry.then_some(sokkel_tui::PanelTool::Retry));
            (&p).render(r.intersection(area), f.buffer_mut());
            self.panel_at = *r;
            if self.tiles().is_empty() {
                return;
            }
            let body = p.layout(*r).body;
            self.table_at = body;
            let columns = Self::instance_columns();
            let rows = self.instance_rows();
            let focused = cx.focus.stop_passing(FocusId::new(INSTANCES), body) && cx.active;
            let table = Table::new(look, &columns, &rows)
                .framed(false)
                .focused(focused);
            f.render_stateful_widget(&table, body.intersection(area), &mut self.instances);
        }
    }

    fn instance_header<'a>(&self, look: &'a Look, tile: &Tile) -> PageHeader<'a> {
        PageHeader::new(look, label(tile).to_owned())
            .lead_status(tile_state(tile))
            .code(tile.url.clone())
            .description(platform(tile))
            .meta([
                format!("checked {}", clock(tile.last_refresh)),
                format!("{}/{} checks OK", tile.ok_count, tile.total_count),
            ])
    }

    fn instance_tiles<'a>(&self, look: &'a Look, tile: &Tile) -> Vec<StatPanel<'a>> {
        let latencies: Vec<f64> = tile
            .history
            .iter()
            .filter_map(|p| p.latency_ms.map(|v| v as f64))
            .collect();
        let good_runs = tile
            .history
            .iter()
            .filter(|p| p.status == Status::Ok)
            .count();
        let stale = self.stale();
        let mark = |p: StatPanel<'a>| match &stale {
            Some(since) => p.stale(since.clone()),
            None => p,
        };
        vec![
            mark(
                StatPanel::new(look, "Checks OK", Some(tile.ok_count as f64))
                    .unit(format!("of {}", tile.total_count))
                    .note(match tile.total_count - tile.ok_count {
                        0 => "all passing".to_owned(),
                        k => format!("{k} not OK"),
                    })
                    .color(match health(tile) {
                        Health::Down => "critical",
                        Health::Warning => "warning",
                        _ => "good",
                    }),
            ),
            mark(
                StatPanel::new(look, "Ping uptime", tile.uptime_pct)
                    .unit("%")
                    .decimals(1)
                    .thresholds(uptime_scale())
                    .note(format!("{} of {} pings", tile.ping_ok, tile.ping_total)),
            ),
            mark(
                StatPanel::new(look, "Latency", tile.latency_ms.map(|v| v as f64))
                    .unit("ms")
                    .sparkline(latencies)
                    .note("mean of the checks that ran"),
            ),
            mark(
                StatPanel::new(look, "Clean refreshes", Some(good_runs as f64))
                    .unit(format!("of {}", tile.history.len()))
                    .note("with every check OK"),
            ),
        ]
    }

    fn instance_spans(&self, look: &Look, tile: &Tile) -> Vec<(Span, u16)> {
        let th = self
            .instance_tiles(look, tile)
            .iter()
            .map(StatPanel::height)
            .max()
            .unwrap_or(6);
        let mut spans: Vec<(Span, u16)> = (0..4).map(|_| (Span::Quarter, th)).collect();
        spans.push((Span::Full, (tile.checks.len() as u16).max(1) + 4));
        spans
    }

    fn draw_instance(&mut self, f: &mut Frame, page: Rect, cx: &mut Cx, name: &str) {
        let look = cx.look;
        let Some(tile) = self.tile(name).cloned() else {
            let header = PageHeader::new(look, name.to_owned())
                .description("This instance is no longer in the config.");
            f.render_widget(&header, page);
            return;
        };
        let header = self.instance_header(look, &tile);
        let hh = header.height(page.width);
        f.render_widget(&header, Rect { height: hh, ..page });
        let area = Rect {
            y: page.y + hh + 1,
            height: page.height.saturating_sub(hh + 1),
            ..page
        };
        let rects = grid(look, area, &self.instance_spans(look, &tile));
        let stats = self.instance_tiles(look, &tile);
        for (s, r) in stats.iter().zip(&rects) {
            s.render(r.intersection(area), f.buffer_mut());
        }
        if let Some(r) = rects.get(stats.len()) {
            let (columns, rows) = (Self::check_columns(), Self::check_rows(&tile));
            self.checks_at = r.intersection(area);
            let focused = cx.focus.stop_passing(FocusId::new(CHECKS), self.checks_at) && cx.active;
            let table = Table::new(look, &columns, &rows).focused(focused);
            f.render_stateful_widget(&table, self.checks_at, &mut self.checks);
        }
    }

    fn check_columns() -> Vec<Column> {
        vec![
            Column::new("Check").width(ColumnWidth::Content),
            Column::new("Status"),
            Column::new("Message").width(ColumnWidth::Fill(1)),
        ]
    }

    fn check_rows(tile: &Tile) -> Vec<Row> {
        tile.checks
            .iter()
            .map(|c| {
                Row::new([
                    Cell::text(&c.name),
                    Cell::toned(c.status.as_str(), tone(c.status)),
                    Cell::text(&c.message),
                ])
                .tone(row_tone(c.status))
            })
            .collect()
    }

    /// The check opened on the instance page (Enter or a press on its row), with its instance.
    fn opened_check(&self) -> Option<(&Tile, &CheckRow)> {
        let Route::Instance(name) = &self.route else {
            return None;
        };
        let tile = self.tile(name)?;
        Some((tile, tile.checks.get(self.checks.opened?)?))
    }

    /// The side panel: the opened check in full, with its instance.
    fn check_pane<'a>(&self, look: &'a Look, ring: [bool; 2]) -> Option<DetailPane<'a>> {
        let (tile, check) = self.opened_check()?;
        Some(
            DetailPane::new(look)
                .sections([
                    PaneSection::new("Check")
                        .summary(check.message.clone())
                        .facts([("Name", Some(check.name.clone()))])
                        .status("Status", state_of(check.status)),
                    PaneSection::new("Instance").facts([
                        ("Name", Some(label(tile).to_owned())),
                        ("URL", Some(tile.url.clone())),
                        ("Version", Some(platform(tile))),
                        ("Checked", Some(clock(tile.last_refresh))),
                    ]),
                ])
                .actions([
                    Button::new(look, "Refresh now")
                        .variant(ButtonVariant::Secondary)
                        .focused(ring[0]),
                    Button::new(look, "Close")
                        .variant(ButtonVariant::Outline)
                        .focused(ring[1]),
                ]),
        )
    }

    /// The palette search: every instance's checks by name or message (the instances themselves
    /// are the palette's own Open commands).
    fn find(&self, query: &str) -> SearchAnswer {
        let q = query.to_lowercase();
        let has = |w: &str| w.to_lowercase().contains(&q);
        let checks = self
            .tiles()
            .iter()
            .flat_map(|t| t.checks.iter().enumerate().map(move |(i, c)| (t, i, c)))
            .filter(|(_, _, c)| has(&c.name) || has(&c.message))
            .take(10)
            .map(|(t, i, c)| {
                Command::new(
                    format!("check:{i}:{}", t.name),
                    format!("{} on {}", c.name, label(t)),
                )
                .detail(format!("{}: {}", c.status.as_str(), one_line(&c.message)))
            });
        let group = CommandGroup::new("Checks", checks);
        SearchAnswer {
            query: query.to_owned(),
            groups: if group.commands.is_empty() {
                Vec::new()
            } else {
                vec![group]
            },
            failed: false,
        }
    }

    /// Close the side panel, focus back on the checks it was opened from.
    fn close_pane(&mut self, cx: &mut Cx) {
        self.checks.opened = None;
        cx.focus.set(FocusId::new(CHECKS));
    }

    fn open(&mut self, name: &str) {
        self.route = Route::Instance(name.to_owned());
        self.checks = TableState::default();
    }
}

impl ShellApp for App {
    fn kiosk(&self) -> bool {
        self.kiosk
    }

    fn id(&self) -> Option<&str> {
        Some("chap-checker")
    }

    fn route(&self) -> String {
        match &self.route {
            Route::Overview => "overview".into(),
            Route::Instance(name) => format!("instance/{name}"),
        }
    }

    fn go(&mut self, route: &str) {
        match route.strip_prefix("instance/") {
            Some(name) => self.open(name),
            None => self.route = Route::Overview,
        }
    }

    fn banner(&self) -> Option<(BannerTone, String)> {
        match &self.conn {
            Conn::Disconnected(why) | Conn::Rejected(why) => {
                Some((BannerTone::Critical, why.clone()))
            }
            _ => self.notice.clone(),
        }
    }

    fn chrome<'a>(&'a self, look: &'a Look) -> Chrome<'a> {
        let overview = RailEntry::new("Overview").icon("\u{25eb}");
        let overview = if self.route == Route::Overview {
            overview.active()
        } else {
            overview
        };
        let instances = self.tiles().iter().map(|t| {
            let entry = RailEntry::new(label(t).to_owned()).icon("\u{25aa}").status(
                if t.total_count == 0 {
                    StatusTone::Quiet
                } else {
                    tone(t.worst_status)
                },
                if t.total_count == 0 {
                    "waiting".to_owned()
                } else {
                    t.worst_status.as_str().to_owned()
                },
            );
            if self.route == Route::Instance(t.name.clone()) {
                entry.active()
            } else {
                entry
            }
        });
        let mut trail = vec!["chap-checker".to_owned()];
        match &self.route {
            Route::Overview => trail.push("Overview".into()),
            Route::Instance(name) => {
                trail.push("Instances".into());
                trail.push(
                    self.tile(name)
                        .map_or(name.clone(), |t| label(t).to_owned()),
                );
            }
        }
        let foot = match &self.conn {
            Conn::Live if self.busy => StatusLine::new(look).note("Refreshing", NoteTone::Plain),
            Conn::Live => StatusLine::new(look)
                .note("Live", NoteTone::Live)
                .live(true),
            Conn::Waiting | Conn::NeedsToken => {
                StatusLine::new(look).note("Connecting", NoteTone::Plain)
            }
            Conn::Disconnected(_) => StatusLine::new(look).note("Disconnected", NoteTone::Error),
            Conn::Rejected(_) => StatusLine::new(look).note("Signed out", NoteTone::Warn),
        };
        let checked = self.state.as_ref().and_then(|s| s.last_refresh);
        Chrome {
            rail: Rail::new(look, "C", "chap-checker")
                .fit()
                .cut(Cut::Middle)
                .groups([
                    RailGroup::new(None, [overview]),
                    RailGroup::new(Some("Instances"), instances),
                ]),
            top: TopBar::new(look).trail(trail),
            foot: foot.facts([
                Fact::labeled("from", self.source.clone()).code(),
                Fact::new(format!("checked {}", clock(checked))),
                Fact::new(format!("every {}s", self.interval)),
            ]),
        }
    }

    fn entry(&mut self, group: usize, entry: usize) {
        if group == 0 {
            self.route = Route::Overview;
        } else if let Some(name) = self.tiles().get(entry).map(|t| t.name.clone()) {
            self.open(&name);
        }
    }

    fn page(&mut self, f: &mut Frame, page: Rect, cx: &mut Cx) {
        match self.route.clone() {
            Route::Overview => self.draw_overview(f, page, cx),
            Route::Instance(name) => self.draw_instance(f, page, cx, &name),
        }
    }

    /// The room both pages need for their tables to stay tables (sokkel's 69 columns, plus the
    /// panel's frame and the page's padding): the rail gives way to it before the rows turn into
    /// cards.
    fn page_width(&self) -> Option<u16> {
        Some(PAGE_WIDTH)
    }

    /// The side panel, while a check is opened on the instance page.
    fn panel_width(&self) -> Option<u16> {
        self.opened_check().map(|_| 44)
    }

    fn panel(&mut self, f: &mut Frame, area: Rect, cx: &mut Cx) {
        let ring = PANE_VERBS.map(|id| cx.focus.ring(FocusId::new(id)));
        let Some(pane) = self.check_pane(cx.look, ring) else {
            return;
        };
        self.pane_at = Rect {
            height: pane.height(area.width).min(area.height),
            ..area
        };
        f.render_widget(&pane, self.pane_at);
        for (k, r) in pane.layout(self.pane_at).actions {
            if let Some(id) = PANE_VERBS.get(k) {
                cx.focus.stop(FocusId::new(id), r);
            }
        }
    }

    fn palette_search(&mut self, query: &str) {
        self.found = Some(self.find(query));
    }

    fn palette_answer(&self) -> Option<SearchAnswer> {
        self.found.clone()
    }

    fn palette_open(&mut self, id: &str) {
        if let Some((i, name)) = id
            .strip_prefix("check:")
            .and_then(|rest| rest.split_once(':'))
            && let Ok(i) = i.parse::<usize>()
        {
            self.open(name);
            self.checks.active = i;
            self.checks.opened = Some(i);
        }
    }

    fn page_rows(&self, look: &Look, width: u16) -> Option<u16> {
        let (hh, spans) = match &self.route {
            Route::Overview => (
                self.overview_header(look).height(width),
                self.overview_spans(look),
            ),
            Route::Instance(name) => {
                let tile = self.tile(name)?;
                (
                    self.instance_header(look, tile).height(width),
                    self.instance_spans(look, tile),
                )
            }
        };
        grid(look, Rect::new(0, hh + 1, width, 0), &spans)
            .iter()
            .map(|r| r.bottom())
            .max()
    }

    fn layer_open(&self) -> bool {
        self.prompt.is_some()
    }

    fn layer_scrim(&self) -> bool {
        true
    }

    fn layer(&mut self, f: &mut Frame, cx: &mut Cx) {
        let Some(mut state) = self.prompt.take() else {
            return;
        };
        let p = self.token_prompt(cx.look, cx.window);
        let r = p.area(cx.window, &state);
        p.render(r, f.buffer_mut(), &mut state);
        if let Some(at) = state.field.cursor_position() {
            f.set_cursor_position(at);
        }
        self.prompt_at = r;
        self.prompt = Some(state);
    }

    fn paste(&mut self, text: &str, cx: &mut Cx) {
        if let Some(mut state) = self.prompt.take() {
            self.token_prompt(cx.look, cx.window)
                .paste(&mut state, text);
            self.prompt = Some(state);
        }
    }

    fn commands(&self) -> Vec<CommandGroup> {
        let overview = Command::new("overview", "Overview")
            .icon("\u{25eb}")
            .keys(Keys::Then('g', 'o'));
        let instances = self.tiles().iter().map(|t| {
            let (status, tone) = if t.total_count == 0 {
                ("waiting".to_owned(), CommandTone::Info)
            } else {
                (
                    t.worst_status.as_str().to_owned(),
                    match t.worst_status {
                        Status::Ok => CommandTone::Good,
                        Status::Warn => CommandTone::Warning,
                        Status::Skipped => CommandTone::Info,
                        _ => CommandTone::Critical,
                    },
                )
            };
            Command::new(format!("open:{}", t.name), label(t).to_owned())
                .icon("\u{25aa}")
                .hint(platform(t))
                .meta(status, Some(tone))
                .keywords([t.name.clone(), t.url.clone()])
        });
        let mut actions = vec![
            Command::new("refresh", "Refresh now")
                .icon("\u{21bb}")
                .keys(Keys::Char('r')),
            Command::new("reload", "Reload the config")
                .icon("\u{21ba}")
                .keys(Keys::Mod('r')),
        ];
        if self.source != "local" {
            actions.push(Command::new("signin", "Sign in").icon("\u{2192}"));
        }
        vec![
            CommandGroup::new("Go to", std::iter::once(overview).chain(instances)),
            CommandGroup::new("Actions", actions),
        ]
    }

    fn command(&mut self, id: &str) -> Ran {
        match id {
            "refresh" => {
                let _ = self.asks.send(Ask::Refresh);
            }
            "reload" => {
                let _ = self.asks.send(Ask::Reload);
            }
            "signin" => self.prompt = Some(PromptState::default()),
            "overview" => self.route = Route::Overview,
            other => {
                if let Some(name) = other.strip_prefix("open:") {
                    self.open(name);
                }
            }
        }
        Ran::Done
    }

    fn shortcuts(&self) -> Vec<Binding<String>> {
        vec![
            Binding::new(
                Keys::Then('g', 'o'),
                "overview".to_owned(),
                "Go to the overview",
            ),
            Binding::new(Keys::Char('r'), "refresh".to_owned(), "Refresh now"),
            Binding::new(Keys::Mod('r'), "reload".to_owned(), "Reload the config"),
        ]
    }

    fn key(&mut self, key: KeyPress, cx: &mut Cx) -> Flow {
        if let Some(mut state) = self.prompt.take() {
            let event = self
                .token_prompt(cx.look, cx.window)
                .handle(&mut state, key);
            self.prompted(event, state);
            return Flow::Handled;
        }
        // The side panel's verbs and the failed panel's Retry, from the keyboard.
        if key.is(Key::Enter) || key.is(Key::Char(' ')) {
            if cx.focus.is(FocusId::new(RETRY)) {
                let _ = self.asks.send(Ask::Refresh);
                return Flow::Handled;
            }
            if cx.focus.is(FocusId::new(PANE_VERBS[0])) {
                let _ = self.asks.send(Ask::Refresh);
                return Flow::Handled;
            }
            if cx.focus.is(FocusId::new(PANE_VERBS[1])) {
                self.close_pane(cx);
                return Flow::Handled;
            }
        }
        if let Route::Instance(name) = &self.route {
            if key.is(Key::Escape) && self.checks.opened.is_some() {
                self.close_pane(cx);
                return Flow::Handled;
            }
            if !cx.focus.is(FocusId::new(CHECKS)) {
                return Flow::Ignored;
            }
            let Some(tile) = self.tile(name) else {
                return Flow::Ignored;
            };
            let (columns, rows) = (Self::check_columns(), Self::check_rows(tile));
            let table = Table::new(cx.look, &columns, &rows);
            return match self.checks.handle(&table, key) {
                sokkel_tui::TableEvent::Ignored => Flow::Ignored,
                _ => Flow::Handled,
            };
        }
        if self.route == Route::Overview && cx.focus.is(FocusId::new(INSTANCES)) {
            let (columns, rows) = (Self::instance_columns(), self.instance_rows());
            let table = Table::new(cx.look, &columns, &rows).framed(false);
            match self.instances.handle(&table, key) {
                sokkel_tui::TableEvent::Ignored => {}
                sokkel_tui::TableEvent::Opened(i) => {
                    if let Some(name) = self.tiles().get(i).map(|t| t.name.clone()) {
                        self.open(&name);
                    }
                    return Flow::Handled;
                }
                _ => return Flow::Handled,
            }
        }
        Flow::Ignored
    }

    fn pointer(&mut self, p: Pointer, cx: &mut Cx) {
        if let Some(mut state) = self.prompt.take() {
            let event = if p.kind == PointerKind::Press {
                self.token_prompt(cx.look, cx.window)
                    .press(&mut state, self.prompt_at, p.at)
            } else {
                PromptEvent::Ignored
            };
            self.prompted(event, state);
            return;
        }
        if p.kind == PointerKind::Press
            && self.route == Route::Overview
            && self.instances_panel(cx.look).hit(self.panel_at, p.at)
                == Some(sokkel_tui::PanelTool::Retry)
        {
            let _ = self.asks.send(Ask::Refresh);
            return;
        }
        if p.kind == PointerKind::Press
            && let Some(pane) = self.check_pane(cx.look, [false; 2])
            && self.pane_at.contains(p.at)
        {
            match pane.press(self.pane_at, p.at) {
                Some(0) => {
                    let _ = self.asks.send(Ask::Refresh);
                }
                Some(_) => self.close_pane(cx),
                None => {}
            }
            return;
        }
        if p.kind == PointerKind::Press
            && let Route::Instance(name) = &self.route
            && let Some(tile) = self.tile(name)
        {
            let (columns, rows) = (Self::check_columns(), Self::check_rows(tile));
            let table = Table::new(cx.look, &columns, &rows);
            if let Some(hit) = table.hit(self.checks_at, p.at, &self.checks) {
                table.press(&mut self.checks, hit);
            }
            return;
        }
        if p.kind == PointerKind::Press && self.route == Route::Overview {
            let (columns, rows) = (Self::instance_columns(), self.instance_rows());
            let table = Table::new(cx.look, &columns, &rows).framed(false);
            if let Some(hit) = table.hit(self.table_at, p.at, &self.instances)
                && let sokkel_tui::TableEvent::Opened(i) = table.press(&mut self.instances, hit)
                && let Some(name) = self.tiles().get(i).map(|t| t.name.clone())
            {
                self.open(&name);
            }
        }
    }

    fn tick(&mut self, now: u64) {
        self.now = now;
        let s = lock(&self.shared);
        self.state.clone_from(&s.state);
        self.conn = s.conn.clone();
        self.busy = s.busy;
        self.last_contact = s.last_contact;
        self.notice = s
            .notice
            .as_ref()
            .filter(|(_, _, until)| *until > Utc::now())
            .map(|(t, w, _)| (*t, w.clone()));
        let needs_token = s.conn == Conn::NeedsToken;
        drop(s);
        if needs_token && self.prompt.is_none() {
            self.prompt = Some(PromptState::default());
            lock(&self.shared).conn = Conn::Waiting;
        }
        // The daemon's answer to a token the prompt sent.
        if self.prompt_busy.is_some() {
            match &self.conn {
                Conn::Live => {
                    self.prompt = None;
                    self.prompt_busy = None;
                }
                // Short words in the prompt's status row; the banner over the page says the rest.
                Conn::Rejected(_) => {
                    self.prompt_failed = Some("Not accepted: check the token.".to_owned());
                    self.prompt_busy = None;
                }
                Conn::Disconnected(_) => {
                    self.prompt_failed = Some("The daemon did not answer.".to_owned());
                    self.prompt_busy = None;
                }
                _ => {}
            }
        }
    }
}

/// The sokkel theme for a `[ui] theme`, trying the contract's own name for it first and falling
/// back to the closest theme a sokkel-tui without it has.
pub fn look_for(theme: UiTheme) -> Look {
    let (names, scheme): (&[&str], Scheme) = match theme {
        UiTheme::Phosphor => (&["phosphor", "terminal"], Scheme::Dark),
        UiTheme::Amber => (&["amber"], Scheme::Dark),
        UiTheme::High => (&["phosphor-bright", "contrast"], Scheme::Dark),
        UiTheme::Tokyo => (&["tokyo"], Scheme::Dark),
        UiTheme::Dhis2 => (&["tray", "paper"], Scheme::Light),
    };
    names
        .iter()
        .find_map(|n| Look::new(n, scheme))
        .or_else(|| Look::new("tidevann", scheme))
        .expect("the contract has tidevann")
        .with_depth(Depth::detect())
        .with_charset(Charset::detect())
}

/// Options for [`run`].
pub struct TuiOptions {
    pub interval_s: f64,
    pub kiosk: bool,
    pub theme: UiTheme,
    /// Draw one frame after the first data arrives, `(cols, rows)`, into `<out>.svg` and
    /// `<out>.txt`, instead of taking the terminal.
    pub frame: Option<((u16, u16), std::path::PathBuf, Vec<KeyPress>)>,
}

/// Run the dashboard until the reader quits.
pub fn run(source: Source, options: TuiOptions) -> std::io::Result<()> {
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("tokio runtime starts");
    let shared: SharedRef = Arc::new(Mutex::new(Shared {
        state: None,
        conn: Conn::Waiting,
        busy: false,
        notice: None,
        last_contact: None,
    }));
    let (tx, rx) = mpsc::unbounded_channel();
    let label = match &source {
        Source::Local(_) => "local".to_owned(),
        Source::Connect { url, .. } => url.clone(),
    };
    match source {
        Source::Local(daemon) => {
            rt.spawn(local(daemon, Arc::clone(&shared), rx));
        }
        Source::Connect { url, token } => {
            rt.spawn(connect(
                url,
                token,
                options.interval_s,
                Arc::clone(&shared),
                rx,
            ));
        }
    }
    let app = App {
        shared: Arc::clone(&shared),
        asks: tx,
        source: label,
        interval: options.interval_s,
        kiosk: options.kiosk,
        route: Route::Overview,
        state: None,
        conn: Conn::Waiting,
        busy: false,
        notice: None,
        last_contact: None,
        checks: TableState::default(),
        prompt: None,
        prompt_busy: None,
        prompt_failed: None,
        now: 0,
        prompt_at: Rect::default(),
        instances: overview_order(),
        table_at: Rect::default(),
        panel_at: Rect::default(),
        checks_at: Rect::default(),
        pane_at: Rect::default(),
        found: None,
    };
    let look = look_for(options.theme);
    if let Some((size, out, keys)) = options.frame {
        // Wait for the first answer (data, a failure or a 401), at most two minutes.
        let deadline = std::time::Instant::now() + Duration::from_secs(120);
        while std::time::Instant::now() < deadline {
            let s = lock(&shared);
            let answered = s.state.as_ref().is_some_and(|st| st.last_refresh.is_some())
                || s.conn != Conn::Waiting;
            drop(s);
            if answered {
                break;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        let mut app = app;
        app.tick(0);
        let buf = app::shot(app, look.clone(), size, |_| {}, &keys);
        std::fs::write(out.with_extension("svg"), snapshot::svg_in(&buf, &look))?;
        std::fs::write(out.with_extension("txt"), snapshot::text(&buf))?;
        rt.shutdown_background();
        return Ok(());
    }
    let result = app::run_shell(app, look);
    rt.shutdown_background();
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::daemon::{CheckRow, HistoryPoint};

    fn tile(name: &str, status: Status) -> Tile {
        Tile {
            name: name.into(),
            display_name: None,
            url: format!("https://{name}.example.org"),
            platform: "DHIS2".into(),
            version: Some("2.42.6".into()),
            worst_status: status,
            ok_count: usize::from(status == Status::Ok),
            total_count: 1,
            ping_ok: 3,
            ping_total: 4,
            latency_ms: Some(120),
            uptime_pct: Some(75.0),
            last_refresh: Some(Utc::now()),
            checks: vec![CheckRow {
                name: "ping".into(),
                status,
                symbol: String::new(),
                message: format!("{name} says {}", status.as_str()),
            }],
            history: vec![HistoryPoint {
                status,
                latency_ms: Some(120),
            }],
        }
    }

    fn app(state: Option<DashboardState>, conn: Conn) -> App {
        let (tx, _rx) = mpsc::unbounded_channel();
        let shared = Arc::new(Mutex::new(Shared {
            state,
            conn,
            busy: false,
            notice: None,
            last_contact: Some(Utc::now()),
        }));
        let mut app = App {
            shared,
            asks: tx,
            source: "http://daemon:8765".into(),
            interval: 30.0,
            kiosk: false,
            route: Route::Overview,
            state: None,
            conn: Conn::Waiting,
            busy: false,
            notice: None,
            last_contact: None,
            checks: TableState::default(),
            prompt: None,
            prompt_busy: None,
            prompt_failed: None,
            now: 0,
            prompt_at: Rect::default(),
            instances: overview_order(),
            table_at: Rect::default(),
            panel_at: Rect::default(),
            checks_at: Rect::default(),
            pane_at: Rect::default(),
            found: None,
        };
        app.tick(0);
        app
    }

    fn state() -> DashboardState {
        DashboardState {
            instance_count: 2,
            alerts_enabled: false,
            interval_s: 30.0,
            last_refresh: Some(Utc::now()),
            tiles: vec![tile("prod", Status::Ok), tile("staging", Status::Fail)],
            ui_title: "Fleet".into(),
            ui_theme: "phosphor".into(),
        }
    }

    fn text(app: App, keys: &[&str]) -> String {
        let look = Look::new("tidevann", Scheme::Dark).unwrap();
        let keys: Vec<KeyPress> = keys.iter().filter_map(|k| KeyPress::named(k)).collect();
        snapshot::text(&app::shot(app, look, (150, 40), |_| {}, &keys))
    }

    #[test]
    fn overview_lists_instances_with_their_status() {
        let screen = text(app(Some(state()), Conn::Live), &[]);
        assert!(screen.contains("Fleet"), "{screen}");
        assert!(screen.contains("1 of 2 instance(s) up"), "{screen}");
        assert!(
            screen.contains("prod") && screen.contains("staging"),
            "{screen}"
        );
        assert!(screen.contains("\u{25cf} fail"), "{screen}");
        assert!(screen.contains("75% (3/4)"), "{screen}");
        assert!(screen.contains("Live"), "{screen}");
    }

    #[test]
    fn enter_opens_the_active_instance() {
        let screen = text(app(Some(state()), Conn::Live), &["enter"]);
        assert!(screen.contains("Instances \u{203a} staging"), "{screen}");
        assert!(screen.contains("staging says fail"), "{screen}");
        assert!(screen.contains("https://staging.example.org"), "{screen}");
    }

    #[test]
    fn a_lost_connection_keeps_the_data_under_a_banner() {
        let screen = text(
            app(
                Some(state()),
                Conn::Disconnected("disconnected from http://daemon:8765: refused".into()),
            ),
            &[],
        );
        assert!(
            screen.contains("disconnected from http://daemon:8765: refused"),
            "{screen}"
        );
        assert!(screen.contains("Disconnected"), "{screen}");
        assert!(screen.contains("staging"), "the last data stays: {screen}");
    }

    #[test]
    fn a_401_without_a_token_opens_the_prompt() {
        let screen = text(app(None, Conn::NeedsToken), &[]);
        assert!(screen.contains("Sign in to http://daemon:8765"), "{screen}");
        assert!(screen.contains("Bearer token"), "{screen}");
    }

    #[test]
    fn themes_map_to_sokkel() {
        for theme in [
            UiTheme::Phosphor,
            UiTheme::Amber,
            UiTheme::High,
            UiTheme::Tokyo,
            UiTheme::Dhis2,
        ] {
            let _ = look_for(theme);
        }
        assert!(
            Look::new("phosphor", Scheme::Dark).is_some(),
            "the pinned sokkel-tui carries phosphor"
        );
        assert!(Look::new("phosphor-bright", Scheme::Dark).is_some());
        assert!(Look::new("tray", Scheme::Light).is_some());
    }

    #[test]
    fn enter_on_a_check_opens_it_in_the_side_panel_and_escape_closes_it() {
        let screen = text(app(Some(state()), Conn::Live), &["enter", "enter"]);
        assert!(screen.contains("CHECK"), "{screen}");
        assert!(screen.contains("Refresh now"), "{screen}");
        let screen = text(app(Some(state()), Conn::Live), &["enter", "enter", "esc"]);
        assert!(!screen.contains("Refresh now"), "{screen}");
        assert!(screen.contains("Instances \u{203a} staging"), "{screen}");
    }

    #[test]
    fn the_palette_search_finds_checks_by_message_and_opens_one() {
        let mut app = app(Some(state()), Conn::Live);
        let found = app.find("SAYS FAIL");
        assert_eq!(found.groups.len(), 1);
        let hits = &found.groups[0].commands;
        assert_eq!(hits.len(), 1);
        assert!(app.find("nothing like it").groups.is_empty());
        let id = hits[0].id.clone();
        app.palette_open(&id);
        assert_eq!(app.route(), "instance/staging");
        assert_eq!(
            app.opened_check()
                .map(|(t, c)| (t.name.as_str(), c.name.as_str())),
            Some(("staging", "ping"))
        );
    }

    #[test]
    fn left_goes_back_right_forward_and_g_o_goes_to_the_overview() {
        let screen = text(app(Some(state()), Conn::Live), &["enter", "left"]);
        assert!(
            screen.contains("chap-checker \u{203a} Overview"),
            "{screen}"
        );
        let screen = text(app(Some(state()), Conn::Live), &["enter", "left", "right"]);
        assert!(screen.contains("Instances \u{203a} staging"), "{screen}");
        let screen = text(app(Some(state()), Conn::Live), &["enter", "g", "o"]);
        assert!(
            screen.contains("chap-checker \u{203a} Overview"),
            "{screen}"
        );
    }

    #[test]
    fn failures_come_first_and_the_summary_names_them() {
        let screen = text(app(Some(state()), Conn::Live), &[]);
        let first = screen
            .lines()
            .position(|l| l.contains("staging") && l.contains("fail"))
            .unwrap();
        let second = screen
            .lines()
            .position(|l| l.contains("prod") && l.contains(" ok "))
            .unwrap();
        assert!(first < second, "{screen}");
        assert!(
            screen.contains("1 of 2 instance(s) up (1 down)"),
            "{screen}"
        );
        assert!(screen.contains("ping on staging"), "{screen}");
    }

    #[test]
    fn a_warning_is_up_but_flagged() {
        let mut s = state();
        s.tiles[1] = tile("staging", Status::Warn);
        let screen = text(app(Some(s), Conn::Live), &[]);
        assert!(
            screen.contains("2 of 2 instance(s) up (1 with warnings)"),
            "{screen}"
        );
        assert!(screen.contains("1 with warnings"), "{screen}");
    }

    #[test]
    fn tab_reaches_the_panels_close_and_enter_presses_it() {
        let screen = text(
            app(Some(state()), Conn::Live),
            &["enter", "enter", "tab", "tab"],
        );
        assert!(screen.contains("Close"), "{screen}");
        let screen = text(
            app(Some(state()), Conn::Live),
            &["enter", "enter", "tab", "tab", "enter"],
        );
        assert!(!screen.contains("Refresh now"), "{screen}");
        assert!(screen.contains("Instances \u{203a} staging"), "{screen}");
    }

    #[test]
    fn a_refused_token_says_why_in_the_prompt_and_a_good_one_closes_it() {
        let look = Look::new("tidevann", Scheme::Dark).unwrap();
        let mut a = app(None, Conn::NeedsToken);
        assert!(a.prompt.is_some());
        a.prompted(PromptEvent::Submit("wrong".into()), PromptState::default());
        assert!(a.prompt.is_some() && a.prompt_busy.is_some());
        lock(&a.shared).conn =
            Conn::Rejected("auth rejected by http://daemon:8765: check the token value".into());
        a.tick(1_000);
        assert!(a.prompt.is_some() && a.prompt_busy.is_none());
        let screen = snapshot::text(&app::shot(a, look.clone(), (150, 40), |_| {}, &[]));
        assert!(
            screen.contains("Not accepted: check the token."),
            "{screen}"
        );

        let mut a = app(None, Conn::NeedsToken);
        a.prompted(PromptEvent::Submit("right".into()), PromptState::default());
        {
            let mut s = lock(&a.shared);
            s.state = Some(state());
            s.conn = Conn::Live;
        }
        a.tick(1_000);
        assert!(a.prompt.is_none());
    }

    #[test]
    #[ignore = "writes the refused prompt as an SVG for a visual check"]
    fn render_the_refused_prompt() {
        let look = Look::new("tray", Scheme::Light).unwrap();
        let mut a = app(None, Conn::NeedsToken);
        a.prompted(PromptEvent::Submit("wrong".into()), PromptState::default());
        lock(&a.shared).conn =
            Conn::Rejected("auth rejected by http://daemon:8765: check the token value".into());
        a.tick(1_000);
        let buf = app::shot(a, look.clone(), (150, 40), |_| {}, &[]);
        let out = std::env::var("PROMPT_SVG").unwrap_or_else(|_| "prompt.svg".into());
        std::fs::write(out, snapshot::svg_in(&buf, &look)).unwrap();
    }
}
