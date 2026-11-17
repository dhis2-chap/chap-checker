# Adding checks and plugins

chap-checker is an uptime monitor with compiled-in plugins. The core (the
runner, the state file and alerts, `verify`, `serve` and the TUI) knows
nothing about DHIS2 or chap-core; each kind of server it understands is a
plugin that brings its own checks. All of it lives in the Rust crate under
`crates/chap-checker/`.

Before writing code, check whether the [`http` kind](configuration.md#instance-kinds)
covers it: an expected status, text in the body, or a value in a JSON body
need no code at all.

## Adding a check to an existing plugin

A check is a `CheckDef`: static metadata plus a `run` function. Checks for a
plugin live together, in `crates/chap-checker/src/checks/<plugin>.rs`.

```rust
use std::time::Instant;

use super::{CheckContext, CheckDef, CheckResult, Status, diagnose_status};
use crate::http::TargetKind;

pub static MY_CHECK: CheckDef = CheckDef {
    name: "dhis2_chap_my_check",
    description: "One line, shown by `chap-checker checks list`.",
    order: 80,                            // lower runs first; built-ins use multiples of 10
    requires: &["dhis2_chap_route"],      // must be OK first, or this one is SKIPPED
    kinds: &[TargetKind::Dhis2],          // the plugins it belongs to
    run: |ctx| Box::pin(my_check(ctx)),
};

async fn my_check(ctx: &mut CheckContext) -> CheckResult {
    const NAME: &str = "dhis2_chap_my_check";
    const PATH: &str = "/api/something";
    let start = Instant::now();
    let response = match ctx.client.get(PATH).await {
        Ok(r) => r,
        // A transport failure (DNS, TLS, timeout) is an ERROR, never a panic.
        Err(e) => return CheckResult::new(NAME, Status::Error, e.describe(Some(PATH))).since(start),
    };
    // 401, 403 (naming the authority it needs) and 404 (saying what it means here) read the
    // same in every check.
    if let Some((message, details)) = diagnose_status(
        response.status,
        PATH,
        Some("/api/something returned 404 - this DHIS2 version doesn't have it."),
        Some("M_dhis-web-app-management"),
    ) {
        return CheckResult::new(NAME, Status::Fail, message).details(details).since(start);
    }
    let Some(body) = response.json() else {
        return CheckResult::new(NAME, Status::Fail, "The body was not JSON.").since(start);
    };
    // ... judge the body ...
    CheckResult::new(NAME, Status::Ok, "All good.").since(start)
}
```

Then register it in `crates/chap-checker/src/checks/mod.rs` (the `REGISTRY`
list) and add a test against a mock server in `tests/checks.rs`.

What the run context gives a check:

| Field                  | What it is                                                                      |
| ---------------------- | ------------------------------------------------------------------------------- |
| `ctx.client`           | The target's client: `get(path)` and `get_with(path, params, headers)` return the response without failing on 4xx/5xx, so a status is a fact to report. Auth, timeouts, redirects and retries are the plugin's. |
| `ctx.target`           | The target: its kind, base URL, timeout, TLS setting, and `params` (the plugin's own config fields). |
| `ctx.prior_results`    | Every check that ran before this one, by name, in any status. A check can judge another's result without a request of its own (the `caps_chap` check does). |
| `ctx.dhis2_version`    | The DHIS2 minor version (`42`), once `dhis2_system_info` has run.              |

House rules for checks:

- **Treat a 2xx as a claim, not proof.** An SSO or reverse proxy answers 200
  with a login page; check the content type and the body's shape before
  reporting `OK`.
- **Never panic.** A non-JSON body, a missing field or a field of the wrong
  type is a `FAIL` with a message that says what was expected. (A panic is
  caught and recorded as `ERROR`, but that's a bug.)
- **Messages say what to do.** Name the path, the status, and the likely
  cause. Never put a credential in a message or in `details`.
- **`details` are for machines.** Alert receivers and the JSON output route on
  them: carry `http_status` and `path` on every failure (`diagnose_status`
  does).

## Adding a plugin

A plugin is a new kind of server, `kind = "..."` in an instance:

1. **The kind.** A `TargetKind` variant in `crates/chap-checker/src/http.rs`
   (with its name in `as_str`) and an entry in `PLUGINS` in
   `crates/chap-checker/src/plugins.rs`: its label for the dashboards, a
   description, the check that counts as its ping (uptime), the check and
   `details` key its version comes from, and a TOML example for
   `chap-checker plugins list`.
2. **Its checks.** In `crates/chap-checker/src/checks/<plugin>.rs`, each with
   `kinds: &[TargetKind::<Plugin>]`, registered in `checks/mod.rs`.
3. **Its config.** In `crates/chap-checker/src/config.rs`, the `instance()`
   match: a kind with no settings and no auth reuses `OgsInstanceConfig`;
   one with settings gets its own struct (`deny_unknown_fields`) and passes
   them to its checks as the target's `params`, as `HttpInstanceConfig` does.
4. **Tests and docs.** Mock-server tests in `tests/checks.rs`, a config test
   in `config.rs`, an example in `init_template.toml` and
   `chap-checker.toml.example`, and a section in the
   [Checks](checks.md) and [Configuration](configuration.md) guides.

A unit test in `plugins.rs` checks that every kind has a plugin whose ping and
version checks exist.

## How `requires` interacts with `checks = [...]`

When a user restricts checks (per-instance `checks = ["dhis2_chap_ping"]` or
CLI `--check dhis2_chap_ping`), the runner pulls in the transitive closure of
`requires`. So declaring `requires: &["dhis2_chap_ping"]` on your check means
asking for `dhis2_chap_my_check` alone still pulls in
`dhis2_ping → dhis2_chap_route → dhis2_chap_ping → dhis2_chap_my_check`.
