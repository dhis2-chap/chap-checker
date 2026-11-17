//! The check loop behind `serve` (and later the TUI): per-tile counters, history and the
//! JSON snapshot both surfaces show.
//!
//! The snapshot is the `/api/state` document. Its shape is the one the Python daemon served, so
//! the browser dashboard and `tui --connect` clients work against either implementation.

use std::collections::{BTreeSet, HashMap, VecDeque};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::alerts::{build_alerters, dispatch};
use crate::checks::{CheckResult, Status};
use crate::config::{CheckerConfig, ConfigError, load_config};
use crate::http::TargetKind;
use crate::plugins;
use crate::runner::{RunReport, TargetEntry, run_targets};
use crate::state_store::utc;

/// Past refresh outcomes kept per tile for the uptime strip.
pub const HISTORY_LEN: usize = 30;

/// The worst status present, ERROR first; OK when empty.
pub fn worst(statuses: impl IntoIterator<Item = Status>) -> Status {
    let present: BTreeSet<Status> = statuses.into_iter().collect();
    [
        Status::Error,
        Status::Fail,
        Status::Warn,
        Status::Skipped,
        Status::Ok,
    ]
    .into_iter()
    .find(|s| present.contains(s))
    .unwrap_or(Status::Ok)
}

fn symbol(status: Status) -> &'static str {
    match status {
        Status::Ok => "\u{2713}",
        Status::Warn => "!",
        Status::Fail => "\u{2717}",
        Status::Error => "!!",
        Status::Skipped => "\u{b7}",
    }
}

/// The check that counts as the tile's ping, per kind (the plugin's).
fn ping_check(kind: &str) -> Option<&'static str> {
    TargetKind::parse(kind).map(|k| plugins::plugin(k).ping_check)
}

/// The server version the kind's plugin reads from one of its checks.
pub fn extract_version(results: &[CheckResult], kind: &str) -> Option<String> {
    let (source, field) = plugins::plugin(TargetKind::parse(kind)?).version?;
    let value = results
        .iter()
        .find(|r| r.name == source)?
        .details
        .get(field)?;
    match value {
        serde_json::Value::Null | serde_json::Value::Bool(false) => None,
        serde_json::Value::String(s) if s.is_empty() => None,
        serde_json::Value::String(s) => Some(s.clone()),
        serde_json::Value::Bool(true) => Some("True".into()),
        other => Some(other.to_string()),
    }
}

/// What decides whether a tile's history still describes the same server: kind, URL, checks.
///
/// Credentials and display name are left out on purpose: rotating a password or relabelling a
/// tile keeps history.
type Identity = (String, String, Option<Vec<String>>);

fn identity(entry: &TargetEntry) -> Identity {
    (
        entry.target.kind.as_str().to_owned(),
        entry.target.url_string().trim_end_matches('/').to_owned(),
        entry.check_names.clone(),
    )
}

/// One per-check row on a tile.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckRow {
    pub name: String,
    pub status: Status,
    pub symbol: String,
    pub message: String,
}

/// One past refresh: the worst status and the average latency of the checks that ran.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HistoryPoint {
    pub status: Status,
    pub latency_ms: Option<i64>,
}

/// One tile of the snapshot.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Tile {
    pub name: String,
    pub display_name: Option<String>,
    pub url: String,
    pub platform: String,
    pub version: Option<String>,
    pub worst_status: Status,
    pub ok_count: usize,
    pub total_count: usize,
    pub ping_ok: u64,
    pub ping_total: u64,
    pub latency_ms: Option<i64>,
    pub uptime_pct: Option<f64>,
    #[serde(serialize_with = "opt_utc", deserialize_with = "opt_utc_in", default)]
    pub last_refresh: Option<DateTime<Utc>>,
    pub checks: Vec<CheckRow>,
    pub history: Vec<HistoryPoint>,
}

/// The whole dashboard: `GET /api/state`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DashboardState {
    pub instance_count: usize,
    pub alerts_enabled: bool,
    pub interval_s: f64,
    #[serde(serialize_with = "opt_utc", deserialize_with = "opt_utc_in", default)]
    pub last_refresh: Option<DateTime<Utc>>,
    pub tiles: Vec<Tile>,
    pub ui_title: String,
    pub ui_theme: String,
}

fn opt_utc_in<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<DateTime<Utc>>, D::Error> {
    let text: Option<String> = Option::deserialize(deserializer)?;
    text.map(|t| {
        DateTime::parse_from_rfc3339(&t)
            .map(|d| d.with_timezone(&Utc))
            .map_err(serde::de::Error::custom)
    })
    .transpose()
}

fn opt_utc<S: serde::Serializer>(
    value: &Option<DateTime<Utc>>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    match value {
        Some(v) => utc::serialize(v, serializer),
        None => serializer.serialize_none(),
    }
}

/// Counters for one tile. They live in memory only: the dashboard is a live view.
#[derive(Debug, Clone, Default)]
struct Tracker {
    identity: Option<Identity>,
    ping_ok: u64,
    ping_total: u64,
    last_refresh: Option<DateTime<Utc>>,
    last_report: Option<RunReport>,
    history: VecDeque<(Status, Option<i64>)>,
}

impl Tracker {
    fn new(identity: Identity) -> Self {
        Self {
            identity: Some(identity),
            ..Self::default()
        }
    }
}

/// What the daemon holds between cycles. Replaced, never mutated, by a reload.
struct Inner {
    targets: Arc<Vec<TargetEntry>>,
    cfg: Arc<CheckerConfig>,
    /// Bumped by every reload, so a cycle can tell the config moved under it.
    generation: u64,
    trackers: HashMap<String, Tracker>,
    last_refresh: Option<DateTime<Utc>>,
    auth_token: Option<String>,
}

impl Inner {
    /// The tracker for `entry`, started fresh when its identity changed.
    fn tracker_for(&mut self, entry: &TargetEntry) -> &mut Tracker {
        let ident = identity(entry);
        let tracker = self
            .trackers
            .entry(entry.name.clone())
            .or_insert_with(|| Tracker::new(ident.clone()));
        match &tracker.identity {
            Some(existing) if *existing != ident => *tracker = Tracker::new(ident),
            None => tracker.identity = Some(ident),
            _ => {}
        }
        tracker
    }
}

/// Outcome of a successful reload.
#[derive(Debug, Clone, PartialEq)]
pub struct Reloaded {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub instance_count: usize,
}

/// Why a reload failed; the message is safe to send to a client.
#[derive(Debug)]
pub enum ReloadError {
    NotFound(String),
    Invalid(String),
}

/// The refresh loop and the state it keeps.
pub struct Daemon {
    inner: Mutex<Inner>,
    refresh: tokio::sync::Mutex<()>,
    pub interval_s: f64,
    pub alerts_enabled: bool,
    pub state_path: Option<PathBuf>,
    pub config_path: Option<PathBuf>,
}

impl Daemon {
    pub fn new(
        targets: Vec<TargetEntry>,
        cfg: CheckerConfig,
        auth_token: Option<String>,
        interval_s: f64,
        alerts_enabled: bool,
        state_path: Option<PathBuf>,
        config_path: Option<PathBuf>,
    ) -> Self {
        let trackers = targets
            .iter()
            .map(|t| (t.name.clone(), Tracker::new(identity(t))))
            .collect();
        Self {
            inner: Mutex::new(Inner {
                targets: Arc::new(targets),
                cfg: Arc::new(cfg),
                generation: 0,
                trackers,
                last_refresh: None,
                auth_token,
            }),
            refresh: tokio::sync::Mutex::new(()),
            interval_s,
            alerts_enabled,
            state_path,
            config_path,
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The bearer token protected routes need right now, if any.
    pub fn auth_token(&self) -> Option<String> {
        self.lock().auth_token.clone()
    }

    pub fn instance_count(&self) -> usize {
        self.lock().targets.len()
    }

    pub fn last_refresh(&self) -> Option<DateTime<Utc>> {
        self.lock().last_refresh
    }

    pub fn ui(&self) -> (String, String) {
        let inner = self.lock();
        (
            inner.cfg.ui.title.clone(),
            inner.cfg.ui.theme.as_str().to_owned(),
        )
    }

    /// Refresh, then sleep `interval_s`, forever.
    pub async fn run_loop(self: Arc<Self>) {
        loop {
            self.refresh_once().await;
            tokio::time::sleep(std::time::Duration::from_secs_f64(self.interval_s)).await;
        }
    }

    /// Run one cycle: check every target, update trackers, dispatch alerts when enabled.
    ///
    /// Cycles never overlap. A call made while one is running waits for it and returns, so
    /// repeated `POST /api/refresh` calls join the running cycle instead of racing it.
    pub async fn refresh_once(&self) {
        let _guard = match self.refresh.try_lock() {
            Ok(guard) => guard,
            Err(_) => {
                let _ = self.refresh.lock().await;
                return;
            }
        };
        let (targets, cfg, generation) = {
            let inner = self.lock();
            (
                Arc::clone(&inner.targets),
                Arc::clone(&inner.cfg),
                inner.generation,
            )
        };
        let started = Utc::now();
        let reports = run_targets(&targets, cfg.concurrency).await;
        let now = Utc::now();
        {
            let mut inner = self.lock();
            if inner.generation != generation {
                // Results computed against the old config would land on the new target list.
                tracing::info!(
                    "refresh: config reloaded mid-cycle, discarding {} stale report(s)",
                    reports.len()
                );
                return;
            }
            let ok = reports.iter().filter(|r| r.ok).count();
            tracing::info!(
                "refresh: {ok}/{} targets ok in {:.0}ms",
                reports.len(),
                (now - started).num_microseconds().unwrap_or(0) as f64 / 1000.0
            );
            for report in &reports {
                let tracker = match targets.iter().find(|e| e.name == report.target_name) {
                    Some(entry) => inner.tracker_for(entry),
                    None => inner
                        .trackers
                        .entry(report.target_name.clone())
                        .or_default(),
                };
                record(tracker, report, now);
            }
            inner.last_refresh = Some(now);
        }
        if let (true, Some(alerts_cfg), Some(state_path)) = (
            self.alerts_enabled,
            cfg.alerts.as_ref(),
            self.state_path.as_ref(),
        ) {
            match build_alerters(alerts_cfg) {
                Ok(alerters) => dispatch(&reports, &targets, &alerters, state_path).await,
                Err(e) => tracing::error!("refresh cycle failed: {e}"),
            }
        }
    }

    /// Re-read the config file and apply it in place.
    ///
    /// Surviving instances keep their history unless their kind, URL or checks changed; removed
    /// ones are dropped. The bearer token is re-resolved first, so a broken `[auth]` block leaves
    /// everything as it was. A cycle in flight discards its results.
    pub fn reload(&self) -> Result<Reloaded, ReloadError> {
        let Some(path) = &self.config_path else {
            return Err(ReloadError::Invalid(
                "No config_path set - server was launched without one.".into(),
            ));
        };
        let cfg = load_config(path).map_err(|e| match e {
            ConfigError::Io(_, ref io) if io.kind() == std::io::ErrorKind::NotFound => {
                ReloadError::NotFound(e.to_string())
            }
            other => ReloadError::Invalid(other.to_string()),
        })?;
        let targets = cfg.target_entries().map_err(ReloadError::Invalid)?;
        let token = match &cfg.auth {
            Some(auth) => Some(auth.resolve_token().map_err(ReloadError::Invalid)?),
            None => None,
        };
        let mut inner = self.lock();
        let old: BTreeSet<String> = inner.targets.iter().map(|t| t.name.clone()).collect();
        let new: BTreeSet<String> = targets.iter().map(|t| t.name.clone()).collect();
        for gone in old.difference(&new) {
            inner.trackers.remove(gone);
        }
        for entry in &targets {
            inner.tracker_for(entry);
        }
        inner.targets = Arc::new(targets);
        inner.cfg = Arc::new(cfg);
        inner.auth_token = token;
        inner.generation += 1;
        Ok(Reloaded {
            added: new.difference(&old).cloned().collect(),
            removed: old.difference(&new).cloned().collect(),
            instance_count: new.len(),
        })
    }

    /// The snapshot both surfaces show.
    pub fn snapshot(&self) -> DashboardState {
        let inner = self.lock();
        DashboardState {
            instance_count: inner.targets.len(),
            alerts_enabled: self.alerts_enabled,
            interval_s: self.interval_s,
            last_refresh: inner.last_refresh,
            tiles: inner
                .targets
                .iter()
                .map(|e| tile(e, inner.trackers.get(&e.name)))
                .collect(),
            ui_title: inner.cfg.ui.title.clone(),
            ui_theme: inner.cfg.ui.theme.as_str().to_owned(),
        }
    }
}

/// Fold one report into a tile's counters.
fn record(tracker: &mut Tracker, report: &RunReport, now: DateTime<Utc>) {
    tracker.last_report = Some(report.clone());
    tracker.last_refresh = Some(now);
    let ping = ping_check(&report.target_kind)
        .and_then(|name| report.results.iter().find(|c| c.name == name));
    if let Some(ping) = ping.filter(|p| p.status != Status::Skipped) {
        tracker.ping_total += 1;
        if ping.status == Status::Ok {
            tracker.ping_ok += 1;
        }
    }
    // A refresh where every check was skipped tells nothing new; it stays out of the strip.
    let ran: Vec<&CheckResult> = report
        .results
        .iter()
        .filter(|c| c.status != Status::Skipped)
        .collect();
    if !ran.is_empty() {
        tracker
            .history
            .push_back((worst(ran.iter().map(|c| c.status)), average_ms(&ran)));
        while tracker.history.len() > HISTORY_LEN {
            tracker.history.pop_front();
        }
    }
}

/// Mean duration, truncated to whole milliseconds like Python's `int()`.
fn average_ms(results: &[&CheckResult]) -> Option<i64> {
    if results.is_empty() {
        return None;
    }
    let total: f64 = results.iter().map(|r| r.duration_ms).sum();
    Some((total / results.len() as f64).trunc() as i64)
}

fn tile(entry: &TargetEntry, tracker: Option<&Tracker>) -> Tile {
    let fresh = Tracker::default();
    // A tracker for a different server (config swapped, no refresh since) shows nothing.
    let t = tracker
        .filter(|t| t.identity.as_ref().is_none_or(|i| *i == identity(entry)))
        .unwrap_or(&fresh);
    let kind = entry.target.kind;
    let history = t
        .history
        .iter()
        .map(|(status, latency_ms)| HistoryPoint {
            status: *status,
            latency_ms: *latency_ms,
        })
        .collect();
    let mut tile = Tile {
        name: entry.name.clone(),
        display_name: entry.display_name.clone(),
        url: entry.target.url_string().trim_end_matches('/').to_owned(),
        platform: kind.platform_label().to_owned(),
        version: None,
        worst_status: Status::Skipped,
        ok_count: 0,
        total_count: 0,
        ping_ok: t.ping_ok,
        ping_total: t.ping_total,
        latency_ms: None,
        uptime_pct: None,
        last_refresh: None,
        checks: Vec::new(),
        history,
    };
    let Some(report) = &t.last_report else {
        return tile;
    };
    let ran: Vec<&CheckResult> = report
        .results
        .iter()
        .filter(|c| c.status != Status::Skipped)
        .collect();
    tile.version = extract_version(&report.results, kind.as_str());
    tile.worst_status = worst(report.results.iter().map(|r| r.status));
    tile.ok_count = report
        .results
        .iter()
        .filter(|r| r.status == Status::Ok)
        .count();
    tile.total_count = report.results.len();
    tile.latency_ms = average_ms(&ran);
    tile.uptime_pct = (t.ping_total > 0).then(|| 100.0 * t.ping_ok as f64 / t.ping_total as f64);
    tile.last_refresh = t.last_refresh;
    tile.checks = report
        .results
        .iter()
        .map(|r| {
            // The plugin's own prefix goes: `dhis2_ping` reads `ping` on a DHIS2 tile.
            let prefix = format!("{}_", kind.as_str());
            let short = r.name.strip_prefix(&prefix).unwrap_or(&r.name);
            CheckRow {
                name: short.to_owned(),
                status: r.status,
                symbol: symbol(r.status).to_owned(),
                message: r.message.clone(),
            }
        })
        .collect();
    tile
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::http::{Auth, Target, TargetKind};

    fn entry() -> TargetEntry {
        TargetEntry {
            name: "prod".into(),
            display_name: None,
            target: Target {
                kind: TargetKind::Dhis2,
                base_url: url::Url::parse("https://prod.example").unwrap(),
                timeout_s: 10.0,
                verify_tls: true,
                auth: Some(Auth::Token("t".into())),
                retry: None,
                params: serde_json::Value::Null,
            },
            check_names: None,
            alerts: vec![],
        }
    }

    fn cfg() -> CheckerConfig {
        CheckerConfig::parse("").unwrap()
    }

    fn daemon() -> Daemon {
        Daemon::new(vec![entry()], cfg(), None, 30.0, true, None, None)
    }

    #[test]
    fn empty_tile_is_skipped() {
        let snap = daemon().snapshot();
        assert_eq!(snap.instance_count, 1);
        let tile = &snap.tiles[0];
        assert_eq!((tile.worst_status, tile.total_count), (Status::Skipped, 0));
        assert_eq!(tile.url, "https://prod.example");
        let json = serde_json::to_value(&snap).unwrap();
        assert_eq!(json["last_refresh"], serde_json::Value::Null);
        assert_eq!(json["ui_theme"], "phosphor");
    }

    #[test]
    fn tile_reflects_tracker() {
        let d = daemon();
        let mut ping = CheckResult::new("dhis2_ping", Status::Ok, "");
        ping.duration_ms = 42.0;
        let mut info = CheckResult::new("dhis2_system_info", Status::Ok, "");
        info.duration_ms = 58.0;
        info.details.insert("version".into(), "2.42.3".into());
        let report = RunReport::new(&entry(), vec![ping, info]);
        {
            let mut inner = d.lock();
            let t = inner.trackers.get_mut("prod").unwrap();
            for _ in 0..3 {
                record(t, &report, Utc::now());
            }
        }
        let tile = &d.snapshot().tiles[0];
        assert_eq!(tile.worst_status, Status::Ok);
        assert_eq!((tile.ok_count, tile.total_count), (2, 2));
        assert_eq!(tile.version.as_deref(), Some("2.42.3"));
        assert_eq!(tile.latency_ms, Some(50));
        assert_eq!(tile.uptime_pct, Some(100.0));
        assert_eq!(
            tile.checks
                .iter()
                .map(|c| c.name.as_str())
                .collect::<Vec<_>>(),
            ["ping", "system_info"]
        );
        assert_eq!(tile.checks[0].symbol, "\u{2713}");
        assert_eq!(tile.history.len(), 3);
    }

    #[test]
    fn worst_ranks_error_first() {
        assert_eq!(
            worst([Status::Ok, Status::Fail, Status::Error]),
            Status::Error
        );
        assert_eq!(worst([Status::Skipped, Status::Ok]), Status::Skipped);
        assert_eq!(worst([]), Status::Ok);
    }

    #[test]
    fn identity_change_resets_history() {
        let d = daemon();
        let report = RunReport::new(
            &entry(),
            vec![CheckResult::new("dhis2_ping", Status::Ok, "")],
        );
        {
            let mut inner = d.lock();
            let e = entry();
            record(inner.tracker_for(&e), &report, Utc::now());
            let mut moved = entry();
            moved.target.base_url = url::Url::parse("https://elsewhere.example").unwrap();
            assert_eq!(inner.tracker_for(&moved).ping_total, 0);
        }
    }
}
