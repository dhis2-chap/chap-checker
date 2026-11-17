//! Generic HTTP reachability: the configured URL answers with a 2xx.

use std::time::Instant;

use reqwest::redirect::Policy;

use super::{CheckContext, CheckDef, CheckResult, Status, details};
use crate::http::{RequestError, TargetKind, build_http};

/// An unauthenticated GET on the target's base URL, following up to five redirects, so a server
/// that redirects `/` to a login page still counts as up. It tells whether DNS, TLS and the proxy
/// in front of the target are healthy, apart from `dhis2_ping`, which can fail for auth reasons
/// alone. It runs first for readability but requires nothing, so either can be turned off.
pub static HTTP_2XX: CheckDef = CheckDef {
    name: "http_2xx",
    description: "Target URL returns a 2xx status code (unauthenticated reachability probe, follows redirects).",
    order: 5,
    requires: &[],
    kinds: &[TargetKind::Dhis2, TargetKind::Ogs],
    run: |ctx| Box::pin(run(ctx)),
};

async fn run(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "http_2xx";
    let url = ctx.target.url_string();
    let start = Instant::now();
    let outcome = async {
        let http = build_http(
            ctx.target.timeout_s,
            ctx.target.verify_tls,
            Policy::limited(5),
        )?;
        let response = http
            .get(&url)
            .send()
            .await
            .map_err(|e| RequestError::from_reqwest(&e))?;
        Ok::<_, RequestError>((response.status().as_u16(), response.url().to_string()))
    }
    .await;
    let (status, final_url) = match outcome {
        Ok(pair) => pair,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(&url))).since(start);
        }
    };
    let d = details([
        ("http_status", status.into()),
        ("final_url", final_url.clone().into()),
    ]);
    let result = if (200..300).contains(&status) {
        CheckResult::new(NAME, Status::Ok, format!("{status} from {final_url}"))
    } else {
        CheckResult::new(
            NAME,
            Status::Fail,
            format!("Unexpected status {status} from {final_url}."),
        )
    };
    result.details(d).since(start)
}
