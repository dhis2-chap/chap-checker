//! chap-checker: health checks for DHIS2 instances integrated with chap-core, and for Open Geo
//! Service deployments.
//!
//! The binary in `main.rs` is a thin clap layer over these modules:
//!
//! - [`config`] loads and validates `chap-checker.toml`.
//! - [`checks`] holds the check registry and every built-in check.
//! - [`runner`] runs checks against targets, several targets at a time.
//! - [`state_store`] persists statuses and computes alert transitions.
//! - [`alerts`] delivers transitions to Slack and generic webhooks.
//! - [`output`] renders a verify report as tables or JSON.
//! - [`daemon`] keeps the refresh loop and the dashboard snapshot; [`serve`] puts it on HTTP.

pub mod alerts;
pub mod checks;
pub mod config;
pub mod daemon;
pub mod http;
pub mod jsonfmt;
pub mod output;
pub mod runner;
pub mod serve;
pub mod state_store;

/// The package version, reported by `--version`, in JSON reports and in webhook envelopes.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
