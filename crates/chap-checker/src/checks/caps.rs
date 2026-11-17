//! The `caps` plugin's checks: CAPS (Climate Automation & Prediction Scheduler).
//!
//! CAPS's `/system/info` reports its own version and whether it reaches chap-core and DHIS2.
//! `caps_system_info` reads it once; `caps_chap` and `caps_dhis2` judge the two connections from
//! that result (through the run context), so a CAPS that is up but cut off from either shows as
//! its own failure rather than as "CAPS is down".

use std::time::Instant;

use serde_json::{Map, Value};

use super::{CheckContext, CheckDef, CheckResult, Status, details, diagnose_status};
use crate::http::TargetKind;

const CAPS: &[TargetKind] = &[TargetKind::Caps];

pub static SYSTEM_INFO: CheckDef = CheckDef {
    name: "caps_system_info",
    description: "CAPS /system/info reachable and reports a version.",
    order: 10,
    requires: &[],
    kinds: CAPS,
    run: |ctx| Box::pin(system_info(ctx)),
};

async fn system_info(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "caps_system_info";
    const PATH: &str = "/system/info";
    let start = Instant::now();
    let response = match ctx
        .client
        .get_with(PATH, &[], &[("Accept", "application/json")])
        .await
    {
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
            "/system/info returned 404 - this server does not look like CAPS (check the base URL).",
        ),
        None,
    ) {
        return CheckResult::new(NAME, Status::Fail, message)
            .details(d)
            .took(elapsed);
    }
    let fail = |message: &str| CheckResult::new(NAME, Status::Fail, message).took(elapsed);
    let Some(body) = response.json() else {
        return fail(
            "/system/info responded with a non-JSON body - a proxy or login page may be in front of CAPS.",
        );
    };
    let Some(caps) = body.get("caps").and_then(Value::as_object) else {
        return fail(
            "Unexpected /system/info response shape (expected a 'caps' object) - the response is not from CAPS.",
        );
    };
    let link = |key: &str| caps.get(key).and_then(Value::as_object);
    let connected = |key: &str| {
        link(key)
            .and_then(|l| l.get("connected"))
            .cloned()
            .unwrap_or(Value::Null)
    };
    let info = |key: &str, field: &str| {
        link(key)
            .and_then(|l| l.get("info"))
            .and_then(|i| i.get(field))
            .cloned()
            .unwrap_or(Value::Null)
    };
    let version = caps
        .get("version")
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty());
    let d: Map<String, Value> = details([
        ("version", version.map_or(Value::Null, Value::from)),
        ("chap_connected", connected("chap")),
        ("chap_core_version", info("chap", "chap_core_version")),
        ("dhis2_connected", connected("dhis")),
        ("dhis2_version", info("dhis", "version")),
    ]);
    match version {
        None => CheckResult::new(
            NAME,
            Status::Warn,
            "CAPS reachable but no 'version' field in response.",
        )
        .details(d)
        .took(elapsed),
        Some(v) => CheckResult::new(NAME, Status::Ok, format!("CAPS {v}."))
            .details(d)
            .took(elapsed),
    }
}

pub static CHAP: CheckDef = CheckDef {
    name: "caps_chap",
    description: "CAPS reports its chap-core connected.",
    order: 20,
    requires: &["caps_system_info"],
    kinds: CAPS,
    run: |ctx| {
        Box::pin(async move { link(ctx, "caps_chap", "chap", "chap-core", "chap_core_version") })
    },
};

pub static DHIS2: CheckDef = CheckDef {
    name: "caps_dhis2",
    description: "CAPS reports its DHIS2 connected.",
    order: 30,
    requires: &["caps_system_info"],
    kinds: CAPS,
    run: |ctx| Box::pin(async move { link(ctx, "caps_dhis2", "dhis2", "DHIS2", "dhis2_version") }),
};

/// Judge one of CAPS's connections from what `caps_system_info` read.
fn link(
    ctx: &CheckContext,
    name: &'static str,
    key: &str,
    label: &str,
    version_key: &str,
) -> CheckResult {
    let Some(info) = ctx.prior_results.get("caps_system_info") else {
        return CheckResult::new(name, Status::Error, "caps_system_info has not run.");
    };
    let connected = info.details.get(&format!("{key}_connected"));
    let version = info.details.get(version_key).and_then(Value::as_str);
    let d = details([
        ("connected", connected.cloned().unwrap_or(Value::Null)),
        (version_key, version.map_or(Value::Null, Value::from)),
    ]);
    match connected {
        Some(Value::Bool(true)) => {
            let suffix = version.map(|v| format!(" {v}")).unwrap_or_default();
            CheckResult::new(name, Status::Ok, format!("{label}{suffix} connected.")).details(d)
        }
        Some(Value::Bool(false)) => CheckResult::new(
            name,
            Status::Fail,
            format!("CAPS reports {label} not connected."),
        )
        .details(d),
        _ => CheckResult::new(
            name,
            Status::Warn,
            format!("CAPS does not report whether {label} is connected."),
        )
        .details(d),
    }
}
