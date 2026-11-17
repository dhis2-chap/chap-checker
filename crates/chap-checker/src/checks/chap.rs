//! The `chap` plugin's checks: a chap-core server reached directly, not through DHIS2.
//!
//! The bodies are judged by the same code as the DHIS2 route's checks (`dhis2_chap_ping`,
//! `dhis2_chap_system_info`), so a chap-core reads the same whichever way it is reached.

use std::time::Instant;

use super::dhis2::{chap_health_body, chap_system_info_body};
use super::{CheckContext, CheckDef, CheckResult, Status, diagnose_status};
use crate::http::TargetKind;

const CHAP: &[TargetKind] = &[TargetKind::Chap];

pub static HEALTH: CheckDef = CheckDef {
    name: "chap_health",
    description: "chap-core /health reports success.",
    order: 10,
    requires: &[],
    kinds: CHAP,
    run: |ctx| Box::pin(health(ctx)),
};

async fn health(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "chap_health";
    const PATH: &str = "/health";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    let elapsed = start.elapsed().as_secs_f64() * 1000.0;
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some(
            "/health returned 404 - this server does not look like chap-core (check the base URL).",
        ),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    chap_health_body(NAME, PATH, &response, elapsed)
}

pub static SYSTEM_INFO: CheckDef = CheckDef {
    name: "chap_system_info",
    description: "chap-core /system/info reports a version.",
    order: 20,
    requires: &["chap_health"],
    kinds: CHAP,
    run: |ctx| Box::pin(system_info(ctx)),
};

async fn system_info(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "chap_system_info";
    const PATH: &str = "/system/info";
    // chap-core 1.1.x - 1.2.0 served it at `/system-info`; tried only when the current path 404s.
    const LEGACY_PATH: &str = "/system-info";
    let start = Instant::now();
    let mut response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        Err(e) => {
            return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start);
        }
    };
    if response.status == 404
        && let Ok(legacy) = ctx.client.get(LEGACY_PATH).await
        && (200..300).contains(&legacy.status)
    {
        response = legacy;
    }
    let elapsed = start.elapsed().as_secs_f64() * 1000.0;
    if let Some((message, d)) = diagnose_status(
        response.status,
        PATH,
        Some("/system/info returned 404 - this chap-core has no /system/info endpoint."),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    chap_system_info_body(NAME, &response, elapsed)
}
