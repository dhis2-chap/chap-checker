//! Run checks against targets: checks in sequence per target, targets several at a time.

use std::sync::Arc;

use chrono::{DateTime, Utc};
use futures::FutureExt;
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use tokio::sync::Semaphore;

use crate::checks::{CheckContext, CheckDef, CheckResult, Status, resolve_checks};
use crate::http::Target;

/// A named target ready to be checked.
///
/// `check_names` restricts which checks run (`None` is every check for the kind; a list is
/// resolved with its transitive `requires`). `alerts` names the alerters this target opts in to.
#[derive(Debug, Clone)]
pub struct TargetEntry {
    pub name: String,
    pub display_name: Option<String>,
    pub target: Target,
    pub check_names: Option<Vec<String>>,
    pub alerts: Vec<String>,
}

/// Counts of results by status.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct TargetSummary {
    pub ok: usize,
    pub warn: usize,
    pub fail: usize,
    pub error: usize,
    pub skipped: usize,
}

/// Results for one target.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunReport {
    pub target_name: String,
    pub target_display_name: Option<String>,
    pub target_kind: String,
    pub target_url: String,
    pub results: Vec<CheckResult>,
    /// True when every check came back `OK`.
    pub ok: bool,
    pub summary: TargetSummary,
}

impl RunReport {
    pub fn new(entry: &TargetEntry, results: Vec<CheckResult>) -> Self {
        let mut summary = TargetSummary::default();
        for r in &results {
            match r.status {
                Status::Ok => summary.ok += 1,
                Status::Warn => summary.warn += 1,
                Status::Fail => summary.fail += 1,
                Status::Error => summary.error += 1,
                Status::Skipped => summary.skipped += 1,
            }
        }
        Self {
            target_name: entry.name.clone(),
            target_display_name: entry.display_name.clone(),
            target_kind: entry.target.kind.as_str().to_owned(),
            target_url: entry.target.url_string(),
            ok: results.iter().all(|r| r.status == Status::Ok),
            results,
            summary,
        }
    }
}

/// The report for one `chap-checker verify` run.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerifyReport {
    pub checker_version: String,
    #[serde(with = "crate::state_store::utc")]
    pub started_at: DateTime<Utc>,
    #[serde(with = "crate::state_store::utc")]
    pub finished_at: DateTime<Utc>,
    pub runs: Vec<RunReport>,
    /// True when every target's checks all came back `OK`.
    pub ok: bool,
}

impl VerifyReport {
    pub fn new(
        started_at: DateTime<Utc>,
        finished_at: DateTime<Utc>,
        runs: Vec<RunReport>,
    ) -> Self {
        Self {
            checker_version: crate::VERSION.to_owned(),
            started_at,
            finished_at,
            ok: runs.iter().all(|r| r.ok),
            runs,
        }
    }
}

/// Run `checks` against `target` in order.
///
/// A check whose `requires` includes a result that is not `OK` is skipped without contacting
/// the server. A check that panics is recorded as `ERROR`; one check never breaks the run.
pub async fn run_checks(target: &Target, checks: &[&'static CheckDef]) -> Vec<CheckResult> {
    let client = match target.open() {
        Ok(client) => client,
        Err(e) => {
            return checks
                .iter()
                .map(|c| CheckResult::new(c.name, Status::Error, e.describe(None)))
                .collect();
        }
    };
    let mut ctx = CheckContext {
        target: target.clone(),
        client,
        dhis2_version: None,
        prior_results: IndexMap::new(),
    };
    let mut results = Vec::with_capacity(checks.len());
    for check in checks {
        let failed: Vec<&str> = check
            .requires
            .iter()
            .copied()
            .filter(|req| {
                ctx.prior_results
                    .get(*req)
                    .is_none_or(|r| r.status != Status::Ok)
            })
            .collect();
        let result = if failed.is_empty() {
            tracing::debug!("running check {}", check.name);
            match std::panic::AssertUnwindSafe((check.run)(&mut ctx))
                .catch_unwind()
                .await
            {
                Ok(result) => result,
                Err(panic) => {
                    let text = panic
                        .downcast_ref::<String>()
                        .cloned()
                        .or_else(|| panic.downcast_ref::<&str>().map(|s| (*s).to_owned()))
                        .unwrap_or_default();
                    tracing::error!("check {} crashed: {text}", check.name);
                    CheckResult::new(check.name, Status::Error, format!("Crashed: {text}"))
                }
            }
        } else {
            CheckResult::new(
                check.name,
                Status::Skipped,
                format!("Skipped: {} not OK.", failed.join(", ")),
            )
        };
        ctx.prior_results
            .insert(check.name.to_owned(), result.clone());
        results.push(result);
    }
    results
}

/// Run every entry's checks, up to `concurrency` targets at a time. Reports keep input order.
pub async fn run_targets(targets: &[TargetEntry], concurrency: usize) -> Vec<RunReport> {
    let semaphore = Arc::new(Semaphore::new(concurrency.max(1)));
    let runs = targets.iter().map(|entry| {
        let semaphore = Arc::clone(&semaphore);
        async move {
            let _permit = semaphore
                .acquire()
                .await
                .expect("semaphore is never closed");
            tracing::debug!("running target {}", entry.name);
            let checks =
                resolve_checks(entry.check_names.as_deref(), entry.target.kind).unwrap_or_default();
            let results = run_checks(&entry.target, &checks).await;
            RunReport::new(entry, results)
        }
    });
    futures::future::join_all(runs).await
}
