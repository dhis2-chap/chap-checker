//! Persisted check status and transition detection for stateful alerting.
//!
//! The state file is a small JSON document holding the latest status of every `(target, check)`
//! pair, plus a per-receiver ledger of open alerts. Each run diffs the previous snapshot against
//! the current results and emits a [`Transition`] to a receiver only when that receiver's alert
//! opens or closes, which keeps Slack quiet during a sustained outage. The format is the one the
//! Python implementation wrote (`version: 1`), so an existing state file carries over.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::checks::Status;
use crate::runner::RunReport;

pub const DEFAULT_STATE_FILENAME: &str = "chap-checker.state.json";

/// Timestamps as pydantic printed them: `Z` suffix, microseconds only when non-zero.
pub mod utc {
    use chrono::{DateTime, SecondsFormat, Timelike, Utc};
    use serde::{Deserialize, Deserializer, Serializer};

    pub fn format(value: &DateTime<Utc>) -> String {
        if value.nanosecond() / 1000 == 0 {
            value.to_rfc3339_opts(SecondsFormat::Secs, true)
        } else {
            value.to_rfc3339_opts(SecondsFormat::Micros, true)
        }
    }

    pub fn serialize<S: Serializer>(
        value: &DateTime<Utc>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&format(value))
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<DateTime<Utc>, D::Error> {
        let text = String::deserialize(deserializer)?;
        DateTime::parse_from_rfc3339(&text)
            .map(|d| d.with_timezone(&Utc))
            .map_err(serde::de::Error::custom)
    }
}

/// A status change worth alerting on.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Transition {
    /// `failure` or `recovery`.
    pub kind: TransitionKind,
    pub target_name: String,
    pub target_display_name: Option<String>,
    pub target_url: String,
    pub check_name: String,
    pub previous_status: Status,
    pub current_status: Status,
    pub message: String,
    pub duration_ms: f64,
    #[serde(with = "utc")]
    pub occurred_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TransitionKind {
    Failure,
    Recovery,
}

/// Persisted status of one `(target, check)` pair.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckState {
    pub status: Status,
    #[serde(with = "utc")]
    pub since: DateTime<Utc>,
}

/// The state file.
///
/// `states` holds the last real (never `SKIPPED`) status of every `(target, check)` key.
/// `receivers` is each alerter's ledger of keys it has an open alert for. It is `None` in files
/// written before the ledger existed (0.10.x and earlier); see [`receiver_ledger`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StateFile {
    /// Always 1; a file without the key is read as version 1, as before.
    #[serde(default = "one", deserialize_with = "version_one")]
    pub version: u8,
    #[serde(default)]
    pub states: BTreeMap<String, CheckState>,
    #[serde(default)]
    pub receivers: Option<BTreeMap<String, BTreeMap<String, CheckState>>>,
}

impl Default for StateFile {
    fn default() -> Self {
        Self {
            version: 1,
            states: BTreeMap::new(),
            receivers: None,
        }
    }
}

fn one() -> u8 {
    1
}

fn version_one<'de, D: serde::Deserializer<'de>>(deserializer: D) -> Result<u8, D::Error> {
    let version = u8::deserialize(deserializer)?;
    if version == 1 {
        Ok(version)
    } else {
        Err(serde::de::Error::custom(format!(
            "unsupported state file version {version}"
        )))
    }
}

/// What one receiver (a configured alerter) listens to.
///
/// Only the non-OK members of `notify_on` matter: they form the receiver's alerting set. A
/// recovery is always sent for a failure the receiver was told about, and never otherwise.
/// `targets` is the set of targets that opted in, or `None` for every target.
#[derive(Debug, Clone)]
pub struct ReceiverSpec {
    pub notify_on: BTreeSet<Status>,
    pub targets: Option<BTreeSet<String>>,
}

impl ReceiverSpec {
    pub fn alerting_set(&self) -> BTreeSet<Status> {
        self.notify_on
            .iter()
            .copied()
            .filter(|s| !matches!(s, Status::Ok | Status::Skipped))
            .collect()
    }
}

/// Load the state file, or an empty state when it is missing.
///
/// A corrupt or incompatible file logs a warning and counts as empty: alert bookkeeping must not
/// fail an otherwise fine cron run. The file is left for a human to inspect.
pub fn load_state(path: &Path) -> StateFile {
    let text = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return StateFile::default(),
        Err(e) => return unreadable(path, &e.to_string()),
    };
    match serde_json::from_slice::<StateFile>(&text) {
        Ok(state) => state,
        Err(e) => unreadable(path, &e.to_string()),
    }
}

fn unreadable(path: &Path, why: &str) -> StateFile {
    tracing::warn!(
        "state file {} is unreadable ({why}); treating prior state as empty. \
         Delete or move the file aside if this persists.",
        path.display()
    );
    StateFile::default()
}

/// Write the state file atomically, creating its directory if needed.
///
/// A uniquely named temp file in the same directory is renamed over the target, so concurrent
/// runs never clobber each other's temp file.
pub fn save_state(path: &Path, state: &StateFile) -> std::io::Result<()> {
    let dir = match path.parent() {
        Some(p) if !p.as_os_str().is_empty() => p.to_path_buf(),
        _ => PathBuf::from("."),
    };
    std::fs::create_dir_all(&dir)?;
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut tmp = tempfile::Builder::new()
        .prefix(&format!("{name}."))
        .suffix(".tmp")
        .tempfile_in(&dir)?;
    let text = crate::jsonfmt::to_pretty_sorted(state).map_err(std::io::Error::other)?;
    tmp.write_all(text.as_bytes())?;
    tmp.write_all(b"\n")?;
    tmp.persist(path).map_err(|e| e.error)?;
    Ok(())
}

/// Raised when the alert-state lock stays taken past the timeout.
#[derive(Debug)]
pub struct StateLockTimeout {
    pub lock_path: PathBuf,
    pub timeout: Duration,
}

impl std::fmt::Display for StateLockTimeout {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Could not acquire alert-state lock at {} within {:.1}s. Another chap-checker process is holding \
             the lock; remove it manually only if you are certain no process owns it.",
            self.lock_path.display(),
            self.timeout.as_secs_f64()
        )
    }
}

impl std::error::Error for StateLockTimeout {}

/// An exclusive lock on `<state>.lock`, held across load, compute, dispatch and save.
///
/// Two overlapping runs (a cron tick and a manual run; `tui` and `serve` sharing a state file)
/// would otherwise read the same prior state and send the same alerts twice. The lock is advisory
/// (`flock`), the same lock the Python implementation took, so the two can share a state file.
/// When the lock file cannot be opened, the run proceeds unlocked with a warning.
pub struct StateLock {
    _file: Option<File>,
}

impl StateLock {
    pub async fn acquire(state_path: &Path, timeout: Duration) -> Result<Self, StateLockTimeout> {
        let mut lock_path = state_path.as_os_str().to_owned();
        lock_path.push(".lock");
        let lock_path = PathBuf::from(lock_path);
        if let Some(parent) = lock_path.parent().filter(|p| !p.as_os_str().is_empty()) {
            let _ = std::fs::create_dir_all(parent);
        }
        let file = match OpenOptions::new()
            .create(true)
            .append(true)
            .read(true)
            .open(&lock_path)
        {
            Ok(f) => f,
            Err(e) => {
                tracing::warn!(
                    "alert-state lock at {} could not be opened ({e}); proceeding without it",
                    lock_path.display()
                );
                return Ok(Self { _file: None });
            }
        };
        let deadline = Instant::now() + timeout;
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(Self { _file: Some(file) }),
                Err(std::fs::TryLockError::WouldBlock) => {
                    if Instant::now() >= deadline {
                        return Err(StateLockTimeout { lock_path, timeout });
                    }
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
                Err(std::fs::TryLockError::Error(e)) => {
                    tracing::warn!(
                        "alert-state lock at {} failed ({e}); proceeding without it",
                        lock_path.display()
                    );
                    return Ok(Self { _file: None });
                }
            }
        }
    }
}

fn state_key(target: &str, check: &str) -> String {
    format!("{target}::{check}")
}

/// The open-alert ledger receiver `name` starts a run with.
///
/// A tracked receiver gets its stored ledger; a receiver new to a current-format file starts
/// empty, so whatever is failing now is announced. A legacy file (no ledgers) seeds the ledger
/// with every key whose last status is in the receiver's alerting set, so an outage in flight
/// during an upgrade neither re-alerts nor loses its recovery.
pub fn receiver_ledger(
    previous: &StateFile,
    name: &str,
    spec: &ReceiverSpec,
) -> BTreeMap<String, CheckState> {
    match &previous.receivers {
        None => {
            let alerting = spec.alerting_set();
            previous
                .states
                .iter()
                .filter(|(_, s)| alerting.contains(&s.status))
                .map(|(k, s)| (k.clone(), s.clone()))
                .collect()
        }
        Some(receivers) => receivers.get(name).cloned().unwrap_or_default(),
    }
}

/// Diff `previous` against `reports` for every receiver.
///
/// For one receiver with alerting set `A` and an observed status `s`:
///
/// - `s` in `A` with no open alert: send a failure and open the alert (whether `s` came from OK
///   or from a non-OK status outside `A`).
/// - `s` is OK with an alert open: send a recovery and close it.
/// - Anything else is silent: a move inside `A`, a move from `A` to a non-OK status outside it
///   (the alert stays open until OK), OK to OK, or a status outside `A` with nothing open.
/// - `SKIPPED` changes nothing.
///
/// Targets not opted into a receiver have their keys dropped from its ledger, so opting in later
/// announces an outage still going on instead of sending a lone recovery. Keys and ledgers not
/// seen in this run carry forward unchanged, so partial runs (`--instance`, `--check`) and
/// alerters that are commented out for a while never lose state.
pub fn compute_receiver_transitions(
    previous: &StateFile,
    reports: &[RunReport],
    receivers: &BTreeMap<String, ReceiverSpec>,
    now: DateTime<Utc>,
) -> (BTreeMap<String, Vec<Transition>>, StateFile) {
    let mut transitions: BTreeMap<String, Vec<Transition>> = receivers
        .keys()
        .map(|name| (name.clone(), Vec::new()))
        .collect();
    let mut ledgers: BTreeMap<String, BTreeMap<String, CheckState>> = receivers
        .iter()
        .map(|(name, spec)| (name.clone(), receiver_ledger(previous, name, spec)))
        .collect();
    let alerting: BTreeMap<&String, BTreeSet<Status>> = receivers
        .iter()
        .map(|(name, spec)| (name, spec.alerting_set()))
        .collect();
    let mut new_states: BTreeMap<String, CheckState> = BTreeMap::new();

    for report in reports {
        for result in &report.results {
            let key = state_key(&report.target_name, &result.name);
            let prev = previous.states.get(&key);
            let prev_status = prev.map_or(Status::Ok, |p| p.status);
            let current = result.status;
            // SKIPPED never overwrites a real status, and with no prior state it isn't tracked.
            match prev {
                Some(p) if current == Status::Skipped || current == p.status => {
                    new_states.insert(key.clone(), p.clone());
                }
                None if current == Status::Skipped => {}
                _ => {
                    new_states.insert(
                        key.clone(),
                        CheckState {
                            status: current,
                            since: now,
                        },
                    );
                }
            }
            for (name, spec) in receivers {
                let ledger = ledgers.get_mut(name).expect("every receiver has a ledger");
                if spec
                    .targets
                    .as_ref()
                    .is_some_and(|t| !t.contains(&report.target_name))
                {
                    ledger.remove(&key);
                    continue;
                }
                if current == Status::Skipped {
                    continue;
                }
                let is_open = ledger.contains_key(&key);
                let kind = if alerting[name].contains(&current) && !is_open {
                    ledger.insert(
                        key.clone(),
                        CheckState {
                            status: current,
                            since: now,
                        },
                    );
                    TransitionKind::Failure
                } else if current == Status::Ok && is_open {
                    ledger.remove(&key);
                    TransitionKind::Recovery
                } else {
                    continue;
                };
                transitions
                    .get_mut(name)
                    .expect("every receiver has a list")
                    .push(Transition {
                        kind,
                        target_name: report.target_name.clone(),
                        target_display_name: report.target_display_name.clone(),
                        target_url: report.target_url.clone(),
                        check_name: result.name.clone(),
                        previous_status: prev_status,
                        current_status: current,
                        message: result.message.clone(),
                        duration_ms: result.duration_ms,
                        occurred_at: now,
                    });
            }
        }
    }

    for (key, state) in &previous.states {
        new_states
            .entry(key.clone())
            .or_insert_with(|| state.clone());
    }
    let mut new_receivers = previous.receivers.clone().unwrap_or_default();
    new_receivers.extend(ledgers);
    (
        transitions,
        StateFile {
            version: 1,
            states: new_states,
            receivers: Some(new_receivers),
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checks::CheckResult;

    fn now(minute: u32) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(&format!("2026-11-17T10:{minute:02}:00Z"))
            .unwrap()
            .with_timezone(&Utc)
    }

    fn report(target: &str, statuses: &[(&str, Status)]) -> RunReport {
        let results: Vec<CheckResult> = statuses
            .iter()
            .map(|(name, status)| CheckResult::new(name, *status, "msg"))
            .collect();
        RunReport {
            target_name: target.into(),
            target_display_name: None,
            target_kind: "dhis2".into(),
            target_url: "https://x/".into(),
            ok: results.iter().all(|r| r.status == Status::Ok),
            results,
            summary: Default::default(),
        }
    }

    fn spec(notify_on: &[Status], targets: Option<&[&str]>) -> ReceiverSpec {
        ReceiverSpec {
            notify_on: notify_on.iter().copied().collect(),
            targets: targets.map(|t| t.iter().map(|s| (*s).to_owned()).collect()),
        }
    }

    fn kinds(list: &[Transition]) -> Vec<(TransitionKind, Status)> {
        list.iter().map(|t| (t.kind, t.current_status)).collect()
    }

    /// Feed `steps` statuses of one check through one receiver; return its transitions per step.
    fn run(notify_on: &[Status], steps: &[Status]) -> Vec<Vec<(TransitionKind, Status)>> {
        let receivers = BTreeMap::from([("slack".to_owned(), spec(notify_on, None))]);
        let mut state = StateFile::default();
        let mut out = Vec::new();
        for (i, status) in steps.iter().enumerate() {
            let (t, next) = compute_receiver_transitions(
                &state,
                &[report("a", &[("c", *status)])],
                &receivers,
                now(i as u32),
            );
            out.push(kinds(&t["slack"]));
            state = next;
        }
        out
    }

    use Status::*;
    use TransitionKind::*;

    #[test]
    fn failure_then_silence_then_recovery() {
        let got = run(&[Fail, Error, Warn], &[Ok, Fail, Fail, Error, Ok, Ok]);
        assert_eq!(
            got,
            vec![
                vec![],
                vec![(Failure, Fail)],
                vec![],
                vec![],
                vec![(Recovery, Ok)],
                vec![]
            ]
        );
    }

    #[test]
    fn narrow_filter_alerts_on_entry_and_holds_open() {
        // OK -> WARN -> FAIL with only `fail`: alert on FAIL. FAIL -> WARN keeps it open.
        let got = run(&[Fail], &[Ok, Warn, Fail, Warn, Fail, Ok]);
        assert_eq!(
            got,
            vec![
                vec![],
                vec![],
                vec![(Failure, Fail)],
                vec![],
                vec![],
                vec![(Recovery, Ok)]
            ]
        );
    }

    #[test]
    fn skipped_changes_nothing() {
        let got = run(&[Fail], &[Fail, Skipped, Ok]);
        assert_eq!(
            got,
            vec![vec![(Failure, Fail)], vec![], vec![(Recovery, Ok)]]
        );
        let receivers = BTreeMap::from([("r".to_owned(), spec(&[Fail], None))]);
        let (_, state) = compute_receiver_transitions(
            &StateFile::default(),
            &[report("a", &[("c", Skipped)])],
            &receivers,
            now(0),
        );
        assert!(state.states.is_empty());
    }

    #[test]
    fn targets_not_opted_in_are_dropped_from_ledger() {
        let opted = BTreeMap::from([("r".to_owned(), spec(&[Fail], Some(&["a"])))]);
        let not_opted = BTreeMap::from([("r".to_owned(), spec(&[Fail], Some(&["b"])))]);
        let (t, state) = compute_receiver_transitions(
            &StateFile::default(),
            &[report("a", &[("c", Fail)])],
            &opted,
            now(0),
        );
        assert_eq!(kinds(&t["r"]), vec![(Failure, Fail)]);
        let (t, state) = compute_receiver_transitions(
            &state,
            &[report("a", &[("c", Fail)])],
            &not_opted,
            now(1),
        );
        assert!(t["r"].is_empty());
        assert!(state.receivers.as_ref().unwrap()["r"].is_empty());
        // Opting in again announces the outage still going on.
        let (t, _) =
            compute_receiver_transitions(&state, &[report("a", &[("c", Fail)])], &opted, now(2));
        assert_eq!(kinds(&t["r"]), vec![(Failure, Fail)]);
    }

    #[test]
    fn legacy_file_seeds_ledgers_from_states() {
        let legacy: StateFile = serde_json::from_str(
            r#"{"version": 1, "states": {"a::c": {"status": "fail", "since": "2026-01-01T00:00:00Z"}}}"#,
        )
        .unwrap();
        let receivers = BTreeMap::from([("r".to_owned(), spec(&[Fail], None))]);
        let (t, _) = compute_receiver_transitions(
            &legacy,
            &[report("a", &[("c", Fail)])],
            &receivers,
            now(0),
        );
        assert!(t["r"].is_empty());
        let (t, _) =
            compute_receiver_transitions(&legacy, &[report("a", &[("c", Ok)])], &receivers, now(0));
        assert_eq!(kinds(&t["r"]), vec![(Recovery, Ok)]);
    }

    #[test]
    fn unobserved_keys_and_receivers_carry_forward() {
        let receivers = BTreeMap::from([("r".to_owned(), spec(&[Fail], None))]);
        let (_, state) = compute_receiver_transitions(
            &StateFile::default(),
            &[report("a", &[("c", Fail)]), report("b", &[("c", Fail)])],
            &receivers,
            now(0),
        );
        let other = BTreeMap::from([("w".to_owned(), spec(&[Fail], None))]);
        let (_, state) =
            compute_receiver_transitions(&state, &[report("a", &[("c", Ok)])], &other, now(1));
        assert_eq!(state.states["b::c"].status, Fail);
        assert_eq!(state.receivers.as_ref().unwrap()["r"].len(), 2);
    }

    #[test]
    fn a_file_without_version_is_version_one() {
        let state: StateFile = serde_json::from_str(
            r#"{"states": {"prod::ping": {"status": "fail", "since": "2026-01-01T00:00:00Z"}}}"#,
        )
        .unwrap();
        assert_eq!(state.version, 1);
        assert_eq!(state.states["prod::ping"].status, Status::Fail);
        assert!(serde_json::from_str::<StateFile>(r#"{"version": 2}"#).is_err());
    }

    #[test]
    fn round_trips_through_disk() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join(DEFAULT_STATE_FILENAME);
        assert_eq!(load_state(&path), StateFile::default());
        let receivers = BTreeMap::from([("r".to_owned(), spec(&[Fail], None))]);
        let (_, state) = compute_receiver_transitions(
            &StateFile::default(),
            &[report("a", &[("c", Fail)])],
            &receivers,
            now(0),
        );
        save_state(&path, &state).unwrap();
        assert_eq!(load_state(&path), state);
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(
            text.contains("\"since\": \"2026-11-17T10:00:00Z\""),
            "{text}"
        );
        std::fs::write(&path, "not json").unwrap();
        assert_eq!(load_state(&path), StateFile::default());
    }
}
