//! The check model, the registry of built-in checks, and the helpers they share.
//!
//! A check is a [`CheckDef`]: static metadata plus a `run` function taking the per-target
//! [`CheckContext`]. `order` sets display and execution order (lower first, ties by name; built-in
//! checks use multiples of 10 so new ones fit between). `requires` names checks that must be `OK`
//! first; otherwise the runner records `SKIPPED` without contacting the server, which keeps one
//! failed foundational check from cascading into a page of failures.

use std::collections::BTreeSet;
use std::time::Instant;

use futures::future::BoxFuture;
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::http::{Client, Target, TargetKind};

mod caps;
mod chap;
mod dhis2;
mod http_2xx;
pub mod http_expect;
mod ogs;

/// Outcome of a single check.
///
/// `Skipped` is informational: a prerequisite was not `OK`, so the check did not run. It is not
/// in the default alert `notify_on` and never produces a transition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Ok,
    Warn,
    Fail,
    Error,
    Skipped,
}

impl Status {
    pub const ALL: [Status; 5] = [
        Status::Ok,
        Status::Warn,
        Status::Fail,
        Status::Error,
        Status::Skipped,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Status::Ok => "ok",
            Status::Warn => "warn",
            Status::Fail => "fail",
            Status::Error => "error",
            Status::Skipped => "skipped",
        }
    }

    pub fn parse(text: &str) -> Option<Status> {
        Status::ALL.into_iter().find(|s| s.as_str() == text)
    }
}

impl std::fmt::Display for Status {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Structured result of a single check.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckResult {
    pub name: String,
    pub status: Status,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub details: Map<String, Value>,
    #[serde(default)]
    pub duration_ms: f64,
}

impl CheckResult {
    pub fn new(name: &str, status: Status, message: impl Into<String>) -> Self {
        Self {
            name: name.to_owned(),
            status,
            message: message.into(),
            details: Map::new(),
            duration_ms: 0.0,
        }
    }

    pub fn details(mut self, details: Map<String, Value>) -> Self {
        self.details = details;
        self
    }

    /// Stamp the time elapsed since `start`.
    pub fn since(mut self, start: Instant) -> Self {
        self.duration_ms = start.elapsed().as_secs_f64() * 1000.0;
        self
    }

    pub fn took(mut self, duration_ms: f64) -> Self {
        self.duration_ms = duration_ms;
        self
    }
}

/// Per-target state handed to every check, built up as checks complete.
///
/// `dhis2_version` is the minor version (`42` for 2.42) once `dhis2_system_info` has seen it.
/// `prior_results` holds every check that completed before this one, in any status.
pub struct CheckContext {
    pub target: Target,
    pub client: Client,
    pub dhis2_version: Option<u32>,
    pub prior_results: IndexMap<String, CheckResult>,
}

pub type RunFn = for<'a> fn(&'a mut CheckContext) -> BoxFuture<'a, CheckResult>;

/// One registered check.
pub struct CheckDef {
    pub name: &'static str,
    pub description: &'static str,
    pub order: i32,
    pub requires: &'static [&'static str],
    pub kinds: &'static [TargetKind],
    pub run: RunFn,
}

impl CheckDef {
    pub fn applies_to(&self, kind: TargetKind) -> bool {
        self.kinds.contains(&kind)
    }

    /// The kinds this check applies to, sorted by name.
    pub fn kind_names(&self) -> Vec<&'static str> {
        let mut names: Vec<_> = self.kinds.iter().map(|k| k.as_str()).collect();
        names.sort_unstable();
        names
    }
}

impl std::fmt::Debug for CheckDef {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CheckDef")
            .field("name", &self.name)
            .finish_non_exhaustive()
    }
}

static REGISTRY: &[&CheckDef] = &[
    &http_2xx::HTTP_2XX,
    &dhis2::PING,
    &dhis2::SYSTEM_INFO,
    &dhis2::CHAP_ROUTE,
    &dhis2::CHAP_PING,
    &dhis2::CHAP_SYSTEM_INFO,
    &dhis2::MODELING_APP,
    &dhis2::CLIMATE_APP,
    &ogs::HEALTH,
    &ogs::INFO,
    &chap::HEALTH,
    &chap::SYSTEM_INFO,
    &caps::SYSTEM_INFO,
    &caps::CHAP,
    &caps::DHIS2,
    &http_expect::EXPECT,
];

/// Every registered check, sorted by `(order, name)`.
pub fn all_checks() -> Vec<&'static CheckDef> {
    let mut checks: Vec<&'static CheckDef> = REGISTRY.to_vec();
    checks.sort_by_key(|c| (c.order, c.name));
    checks
}

/// Look up a check by name.
pub fn find(name: &str) -> Option<&'static CheckDef> {
    REGISTRY.iter().copied().find(|c| c.name == name)
}

/// Comma-separated sorted names of every registered check.
pub fn known_names() -> String {
    let names: BTreeSet<&str> = REGISTRY.iter().map(|c| c.name).collect();
    names.into_iter().collect::<Vec<_>>().join(", ")
}

/// Resolve `names` into checks for an instance of `kind`.
///
/// `None` means every check that applies to `kind`. Otherwise: the named checks plus the
/// transitive closure of their `requires`, so a partial selection always has its prerequisites.
/// Named checks that don't apply to `kind` are dropped. Output is in `(order, name)` order.
pub fn resolve_checks(
    names: Option<&[String]>,
    kind: TargetKind,
) -> Result<Vec<&'static CheckDef>, String> {
    let Some(names) = names else {
        return Ok(all_checks()
            .into_iter()
            .filter(|c| c.applies_to(kind))
            .collect());
    };
    fn add(name: &str, selected: &mut BTreeSet<&'static str>) -> Result<(), String> {
        if selected.contains(name) {
            return Ok(());
        }
        let check = find(name)
            .ok_or_else(|| format!("unknown check '{name}'. Known: {}", known_names()))?;
        for req in check.requires {
            add(req, selected)?;
        }
        selected.insert(check.name);
        Ok(())
    }
    let mut selected = BTreeSet::new();
    for name in names {
        if find(name).is_some_and(|c| !c.applies_to(kind)) {
            continue;
        }
        add(name, &mut selected)?;
    }
    Ok(all_checks()
        .into_iter()
        .filter(|c| selected.contains(c.name))
        .collect())
}

/// Return `(message, details)` for a 4xx/5xx status, or `None` below 400.
///
/// 401 means the credentials are no longer valid; 403 means authenticated but missing an
/// authority (named when the caller knows it); 404 is ambiguous on its own, so callers say what it
/// means for their endpoint. Every branch carries `http_status` and `path` in details.
pub fn diagnose_status(
    status: u16,
    path: &str,
    not_found_meaning: Option<&str>,
    required_authority: Option<&str>,
) -> Option<(String, Map<String, Value>)> {
    if status < 400 {
        return None;
    }
    let mut details = Map::new();
    details.insert("http_status".into(), status.into());
    details.insert("path".into(), path.into());
    let message = match status {
        401 => format!("Authentication rejected (401) on {path} - credentials no longer valid."),
        403 => match required_authority {
            Some(authority) => {
                details.insert("required_authority".into(), authority.into());
                format!(
                    "Forbidden (403) on {path} - authenticated but the user lacks the '{authority}' authority needed to read this endpoint."
                )
            }
            None => format!(
                "Forbidden (403) on {path} - authenticated but the user lacks the required authority."
            ),
        },
        404 => not_found_meaning.map(str::to_owned).unwrap_or_else(|| {
            format!("{path} returned 404 - the endpoint is missing on this server.")
        }),
        _ => format!("Unexpected status {status} on {path}."),
    };
    Some((message, details))
}

/// Map a DHIS2 version string (`"2.42.3"`, `"2.44-SNAPSHOT"`) to its minor number.
pub fn parse_dhis2_version(version: &str) -> Option<u32> {
    let mut parts = version.splitn(3, '.');
    let major = parts.next()?;
    let minor = parts.next()?;
    if major.is_empty() || !major.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let digits: String = minor.chars().take_while(char::is_ascii_digit).collect();
    digits.parse().ok()
}

/// Build a details map from `(key, value)` pairs.
pub(crate) fn details<const N: usize>(pairs: [(&str, Value); N]) -> Map<String, Value> {
    pairs.into_iter().map(|(k, v)| (k.to_owned(), v)).collect()
}

/// A copy of `object` without its `null` values (pydantic's `exclude_none`).
pub(crate) fn without_nulls(object: &Map<String, Value>) -> Map<String, Value> {
    object
        .iter()
        .filter(|(_, v)| !v.is_null())
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

/// The string at `key`, when it is a non-empty string.
pub(crate) fn non_empty_str<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(checks: &[&CheckDef]) -> Vec<&'static str> {
        checks.iter().map(|c| c.name).collect()
    }

    #[test]
    fn every_dhis2_check_by_default_in_order() {
        let checks = resolve_checks(None, TargetKind::Dhis2).unwrap();
        assert_eq!(
            names(&checks),
            [
                "http_2xx",
                "dhis2_ping",
                "dhis2_system_info",
                "dhis2_chap_route",
                "dhis2_chap_ping",
                "dhis2_chap_system_info",
                "dhis2_chap_modeling_app",
                "dhis2_chap_climate_app",
            ]
        );
        let ogs = resolve_checks(None, TargetKind::Ogs).unwrap();
        assert_eq!(names(&ogs), ["http_2xx", "ogs_health", "ogs_info"]);
    }

    #[test]
    fn selection_pulls_in_requires() {
        let wanted = vec!["dhis2_chap_system_info".to_owned()];
        let checks = resolve_checks(Some(&wanted), TargetKind::Dhis2).unwrap();
        assert_eq!(
            names(&checks),
            [
                "dhis2_ping",
                "dhis2_chap_route",
                "dhis2_chap_ping",
                "dhis2_chap_system_info"
            ]
        );
    }

    #[test]
    fn selection_drops_other_kinds_and_rejects_unknown() {
        let wanted = vec!["dhis2_ping".to_owned(), "ogs_info".to_owned()];
        let checks = resolve_checks(Some(&wanted), TargetKind::Ogs).unwrap();
        assert_eq!(names(&checks), ["ogs_health", "ogs_info"]);
        let err = resolve_checks(Some(&["nope".to_owned()]), TargetKind::Dhis2).unwrap_err();
        assert!(
            err.starts_with("unknown check 'nope'. Known: caps_chap, caps_dhis2"),
            "{err}"
        );
    }

    #[test]
    fn requires_only_name_registered_checks() {
        for check in all_checks() {
            for req in check.requires {
                assert!(
                    find(req).is_some(),
                    "{} requires unknown {}",
                    check.name,
                    req
                );
            }
        }
    }

    #[test]
    fn versions() {
        assert_eq!(parse_dhis2_version("2.42.3"), Some(42));
        assert_eq!(parse_dhis2_version("2.44-SNAPSHOT"), Some(44));
        assert_eq!(parse_dhis2_version("garbage"), None);
    }

    #[test]
    fn diagnoses() {
        assert!(diagnose_status(200, "/x", None, None).is_none());
        let (msg, details) = diagnose_status(403, "/api/apps", None, Some("M_x")).unwrap();
        assert!(msg.contains("'M_x' authority"));
        assert_eq!(details["required_authority"], "M_x");
        let (msg, _) = diagnose_status(500, "/x", None, None).unwrap();
        assert_eq!(msg, "Unexpected status 500 on /x.");
    }
}
