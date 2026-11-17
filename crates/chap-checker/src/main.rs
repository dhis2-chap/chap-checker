//! The `chap-checker` command line.

use std::collections::BTreeMap;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use chap_checker::alerts::{ALERTER_TYPES, Alerter, build_alerters, dispatch};
use chap_checker::checks::{self, Status, resolve_checks};
use chap_checker::config::{
    CheckerConfig, DEFAULT_CONCURRENCY, DEFAULT_CONFIG_FILENAME, default_config_path, load_config,
};
use chap_checker::http::{Auth, Target, TargetKind};
use chap_checker::output::{new_table, render, styled};
use chap_checker::runner::{TargetEntry, VerifyReport, run_targets};
use chap_checker::state_store::{DEFAULT_STATE_FILENAME, Transition, TransitionKind};
use chap_checker::{VERSION, jsonfmt};
use chrono::Utc;
use clap::parser::ValueSource;
use clap::{ArgMatches, CommandFactory, FromArgMatches, Parser, Subcommand, ValueEnum};
use comfy_table::{Cell, Color};
use serde::Serialize;
use url::Url;

const INIT_TEMPLATE: &str = include_str!("init_template.toml");

/// Health-check CLI for DHIS2 instances integrated with chap-core and Open Geo Service
/// deployments. Cron-friendly with Slack/webhook alerts on status transitions and a TUI dashboard
/// for at-a-glance monitoring.
#[derive(Parser)]
#[command(name = "chap-checker", disable_version_flag = true)]
struct Cli {
    /// Enable verbose debug logging on stderr.
    #[arg(short, long, global = true)]
    debug: bool,
    /// Emit machine-readable JSON instead of a table (cron-friendly).
    #[arg(long, global = true)]
    json: bool,
    /// Suppress stdout entirely; just run checks, dispatch alerts, and exit (cron-friendly).
    #[arg(short, long, global = true)]
    quiet: bool,
    /// Show version and exit.
    #[arg(short = 'v', long)]
    version: bool,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Create a minimal chap-checker.toml in the current directory.
    ///
    /// Drops a single working `instances.play` table pointed at the public DHIS2 demo instance
    /// (admin / district) so you can run `chap-checker verify` immediately and replace the values
    /// once it works.
    Init {
        /// Where to write the new config.
        #[arg(short, long, default_value = DEFAULT_CONFIG_FILENAME)]
        output: PathBuf,
        /// Overwrite the file if it already exists.
        #[arg(short, long)]
        force: bool,
    },
    /// Run every registered check against one or more instances.
    ///
    /// With `--url`, runs in ad-hoc mode against that single DHIS2 URL and ignores any config.
    /// Auth is Basic (`--username` plus a password from `--password`, `--password-env NAME`,
    /// `DHIS2_PASSWORD` or a hidden prompt) or a Personal Access Token (`--token`,
    /// `--token-env NAME` or `DHIS2_TOKEN`). Otherwise the config is loaded from `--config` or
    /// `./chap-checker.toml`, and every instance runs unless `--instance` picks one.
    ///
    /// The exit code is 0 when every check on every target is OK, 1 otherwise. Alerts honor each
    /// instance's `alerts = [...]` opt-in; skip them with `--no-alerts`.
    Verify(VerifyArgs),
    /// Inspect or test configured alerters.
    #[command(subcommand, arg_required_else_help = true)]
    Alerts(AlertsCommand),
    /// Inspect available checks.
    #[command(subcommand, arg_required_else_help = true)]
    Checks(ChecksCommand),
}

#[derive(clap::Args)]
struct VerifyArgs {
    /// Path to a TOML config (defaults to ./chap-checker.toml if present).
    #[arg(short, long, env = "CHAP_CHECKER_CONFIG")]
    config: Option<PathBuf>,
    /// Run only this named instance from the config.
    #[arg(short, long)]
    instance: Option<String>,
    /// Ad-hoc DHIS2 base URL (bypasses config).
    #[arg(long, env = "DHIS2_URL")]
    url: Option<String>,
    /// DHIS2 username (ad-hoc mode).
    #[arg(short, long, env = "DHIS2_USERNAME")]
    username: Option<String>,
    /// DHIS2 password (ad-hoc mode). Prefer --password-env or the interactive prompt: inline
    /// passwords end up in shell history and `ps` output.
    #[arg(short, long, env = "DHIS2_PASSWORD", hide_env_values = true)]
    password: Option<String>,
    /// Name of the env var holding the DHIS2 password (ad-hoc mode). Recommended over --password.
    #[arg(long)]
    password_env: Option<String>,
    /// DHIS2 Personal Access Token (ad-hoc mode). Mutually exclusive with --password /
    /// --password-env. Prefer --token-env to keep the value out of shell history.
    #[arg(long, env = "DHIS2_TOKEN", hide_env_values = true)]
    token: Option<String>,
    /// Name of the env var holding the DHIS2 PAT (ad-hoc mode). Recommended over --token.
    #[arg(long)]
    token_env: Option<String>,
    /// HTTP timeout per request (seconds, ad-hoc mode).
    #[arg(long, default_value_t = 10.0)]
    timeout: f64,
    /// Skip TLS certificate verification (ad-hoc mode).
    #[arg(long)]
    insecure: bool,
    /// Restrict to these check names (transitive `requires` are pulled in). Repeat the flag for
    /// multiple. Mirrors the per-instance `checks = [...]` config field.
    #[arg(long = "check", visible_alias = "checks")]
    check: Vec<String>,
    /// Skip alert dispatch even if configured.
    #[arg(long, visible_alias = "no-alert")]
    no_alerts: bool,
    /// Path to the persisted state file (default: ./chap-checker.state.json next to the config).
    #[arg(long, env = "CHAP_CHECKER_STATE")]
    state: Option<PathBuf>,
    /// Number of targets to check in parallel. Overrides the config value if given. Default 5.
    #[arg(long, value_parser = clap::value_parser!(u16).range(1..=100))]
    concurrency: Option<u16>,
}

#[derive(Subcommand)]
enum AlertsCommand {
    /// List registered alerter types and their TOML fields.
    #[command(alias = "ls")]
    List,
    /// Send a synthetic transition to every configured alerter (or a named one).
    ///
    /// Useful after rotating a credential or when you suspect the alert pipeline is broken. Each
    /// run posts a real message to the configured receiver, so don't put this on a cron. Exit
    /// code is 0 only when every alerter delivered.
    Test {
        /// Send only to this alerter (must be a configured alerter name).
        #[arg(short, long)]
        name: Option<String>,
        /// Which synthetic transition(s) to send.
        #[arg(short, long, value_enum, default_value_t = TestKind::Failure)]
        kind: TestKind,
        /// Path to a TOML config (defaults to ./chap-checker.toml if present).
        #[arg(short, long, env = "CHAP_CHECKER_CONFIG")]
        config: Option<PathBuf>,
    },
}

#[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
enum TestKind {
    Failure,
    Recovery,
    Both,
}

#[derive(Subcommand)]
enum ChecksCommand {
    /// List registered checks.
    #[command(alias = "ls")]
    List,
}

/// Flags every subcommand shares.
struct Global {
    json: bool,
    quiet: bool,
}

/// A usage error: printed as `Error: ...` with exit code 2, like the Python CLI's.
struct Usage(String);

impl<T: Into<String>> From<T> for Usage {
    fn from(message: T) -> Self {
        Usage(message.into())
    }
}

type Outcome = Result<ExitCode, Usage>;

fn main() -> ExitCode {
    let matches = Cli::command().get_matches();
    let cli = match Cli::from_arg_matches(&matches) {
        Ok(cli) => cli,
        Err(e) => e.exit(),
    };
    if cli.version {
        println!("chap-checker {VERSION}");
        return ExitCode::SUCCESS;
    }
    init_logging(cli.debug);
    let global = Global {
        json: cli.json,
        quiet: cli.quiet,
    };
    let outcome = match cli.command {
        None => {
            let _ = Cli::command().print_long_help();
            Ok(ExitCode::SUCCESS)
        }
        Some(Command::Init { output, force }) => init(&global, &output, force),
        Some(Command::Verify(args)) => {
            let sub = matches
                .subcommand_matches("verify")
                .expect("verify was invoked");
            verify(&global, args, sub)
        }
        Some(Command::Alerts(AlertsCommand::List)) => alerts_list(&global),
        Some(Command::Alerts(AlertsCommand::Test { name, kind, config })) => {
            alerts_test(&global, name, kind, config)
        }
        Some(Command::Checks(ChecksCommand::List)) => checks_list(&global),
    };
    match outcome {
        Ok(code) => code,
        Err(Usage(message)) => {
            eprintln!("Error: {message}");
            ExitCode::from(2)
        }
    }
}

/// Logs go to stderr so `--json` on stdout stays machine-readable.
fn init_logging(debug: bool) {
    let level = if debug {
        "chap_checker=debug"
    } else {
        "chap_checker=warn"
    };
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::new(level))
        .with_writer(std::io::stderr)
        .with_target(true)
        .init();
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("tokio runtime starts")
}

fn init(global: &Global, output: &Path, force: bool) -> Outcome {
    if output.exists() && !force {
        return Err(format!(
            "{} already exists. Pass --force to overwrite.",
            output.display()
        )
        .into());
    }
    std::fs::write(output, INIT_TEMPLATE).map_err(|e| format!("{}: {e}", output.display()))?;
    #[cfg(unix)]
    {
        // The template holds an inline password; chmod 600 keeps the loader's advisory quiet.
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(output, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("{}: {e}", output.display()))?;
    }
    if !global.quiet {
        println!("Wrote {}", output.display());
    }
    Ok(ExitCode::SUCCESS)
}

/// The config path to use: the flag, else `./chap-checker.toml` (absolute, for messages).
fn config_path(flag: Option<PathBuf>) -> PathBuf {
    flag.unwrap_or_else(|| {
        std::env::current_dir()
            .map(|d| d.join(default_config_path()))
            .unwrap_or_else(|_| default_config_path())
    })
}

/// The state file: the flag, else `chap-checker.state.json` next to the config.
fn state_path(flag: Option<PathBuf>, config: &Path) -> PathBuf {
    flag.unwrap_or_else(|| {
        config.parent().map_or_else(
            || PathBuf::from(DEFAULT_STATE_FILENAME),
            |p| p.join(DEFAULT_STATE_FILENAME),
        )
    })
}

fn load_or_fail(path: &Path) -> Result<CheckerConfig, Usage> {
    load_config(path).map_err(|e| Usage(format!("invalid config {}:\n{e}", path.display())))
}

fn verify(global: &Global, args: VerifyArgs, matches: &ArgMatches) -> Outcome {
    let explicit = |id: &str| matches.value_source(id) == Some(ValueSource::CommandLine);
    let check_names = (!args.check.is_empty()).then(|| args.check.clone());
    if let Some(names) = &check_names {
        resolve_checks(Some(names), TargetKind::Dhis2)?;
    }

    let (targets, cfg, config_file) = if let Some(url) = &args.url {
        if args.instance.is_some() {
            return Err("--instance cannot be combined with --url; --url is ad-hoc mode.".into());
        }
        let target = adhoc_target(url, &args, &explicit)?;
        let entry = TargetEntry {
            name: "ad-hoc".into(),
            display_name: None,
            target,
            check_names: check_names.clone(),
            alerts: Vec::new(),
        };
        (vec![entry], None, None)
    } else {
        let path = config_path(args.config.clone());
        if !path.exists() {
            return Err(format!(
                "No config at {}. Provide --config <path>, create a ./{DEFAULT_CONFIG_FILENAME}, or use --url for ad-hoc mode.",
                path.display()
            )
            .into());
        }
        let cfg = load_or_fail(&path)?;
        if cfg.instances.is_empty() {
            return Err(
                format!("{} contains no [instances.<name>] entries.", path.display()).into(),
            );
        }
        let mut targets = match &args.instance {
            Some(name) => vec![cfg.get(name)?.to_target_entry(name, cfg.retry.as_ref())?],
            None => cfg.target_entries()?,
        };
        if let Some(names) = &check_names {
            targets = apply_check_override(targets, names)?;
        }
        (targets, Some(cfg), Some(path))
    };

    let concurrency = args
        .concurrency
        .map(usize::from)
        .or(cfg.as_ref().map(|c| c.concurrency))
        .unwrap_or(DEFAULT_CONCURRENCY);
    let rt = runtime();
    let started_at = Utc::now();
    let reports = rt.block_on(run_targets(&targets, concurrency));
    let finished_at = Utc::now();

    if !args.no_alerts
        && let (Some(cfg), Some(path)) = (&cfg, &config_file)
        && let Some(alerts_cfg) = &cfg.alerts
    {
        let alerters = build_alerters(alerts_cfg)?;
        let state = state_path(args.state.clone(), path);
        rt.block_on(dispatch(&reports, &targets, &alerters, &state));
    }

    let report = VerifyReport::new(started_at, finished_at, reports);
    if !global.quiet {
        render(&report, global.json);
    }
    Ok(if report.ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    })
}

/// Apply `--check` across targets of possibly different kinds; drop targets left with nothing.
fn apply_check_override(
    targets: Vec<TargetEntry>,
    names: &[String],
) -> Result<Vec<TargetEntry>, Usage> {
    let mut kept = Vec::new();
    for mut t in targets {
        if resolve_checks(Some(names), t.target.kind)?.is_empty() {
            eprintln!(
                "note: skipping instance '{}' (kind '{}'): none of {} apply to it.",
                t.name,
                t.target.kind.as_str(),
                names.join(", ")
            );
        } else {
            t.check_names = Some(names.to_vec());
            kept.push(t);
        }
    }
    if kept.is_empty() {
        return Err(format!(
            "none of {} apply to the selected instance(s).",
            names.join(", ")
        )
        .into());
    }
    Ok(kept)
}

/// Build the single DHIS2 target for `verify --url`.
///
/// Flags given on the command line decide the auth mode; passing both token and password flags
/// is an error. With no auth flag, whichever of `DHIS2_TOKEN` / `DHIS2_PASSWORD` is set decides,
/// and both being set is an error so cron jobs never flip modes between hosts.
fn adhoc_target(
    url: &str,
    args: &VerifyArgs,
    explicit: &dyn Fn(&str) -> bool,
) -> Result<Target, Usage> {
    let token_flag = explicit("token") || explicit("token_env");
    let password_flag = explicit("password") || explicit("password_env") || explicit("username");
    if token_flag && password_flag {
        return Err(
            "Pass either password flags (--password / --password-env / --username) \
                    or token flags (--token / --token-env), not both."
                .into(),
        );
    }
    let has_token = args.token.is_some() || args.token_env.is_some();
    let has_password = args.password.is_some() || args.password_env.is_some();
    let use_token = if token_flag {
        true
    } else if password_flag {
        false
    } else if has_token && has_password {
        return Err("DHIS2_TOKEN and DHIS2_PASSWORD are both set in the environment. \
                    Pass --token-env or --password-env (or --token / --password) to pick a mode explicitly."
            .into());
    } else {
        has_token
    };
    let base_url = Url::parse(url)
        .ok()
        .filter(|u| matches!(u.scheme(), "http" | "https") && u.host_str().is_some())
        .ok_or_else(|| Usage(format!("--url: not a valid http(s) URL: {url}")))?;
    let auth = if use_token {
        Auth::Token(secret(
            "token",
            args.token.as_deref(),
            args.token_env.as_deref(),
            explicit("token"),
            "DHIS2 token",
            "--url with --token requires a value. Pass --token-env NAME, set DHIS2_TOKEN, \
             or run interactively for a hidden prompt.",
        )?)
    } else {
        let Some(username) = args.username.clone() else {
            return Err("--url requires --username (with a password) or one of --token / --token-env / DHIS2_TOKEN.".into());
        };
        let password = secret(
            "password",
            args.password.as_deref(),
            args.password_env.as_deref(),
            explicit("password"),
            "DHIS2 password",
            "--url requires a password. Pass --password-env NAME, set DHIS2_PASSWORD, \
             or run interactively for a hidden prompt.",
        )?;
        Auth::Basic { username, password }
    };
    Ok(Target {
        kind: TargetKind::Dhis2,
        base_url,
        timeout_s: args.timeout,
        verify_tls: !args.insecure,
        auth: Some(auth),
        retry: None,
    })
}

/// Pick a secret from `--<flag>-env`, then `--<flag>` (or its env var), then a hidden prompt.
fn secret(
    flag: &str,
    value: Option<&str>,
    env_name: Option<&str>,
    value_explicit: bool,
    prompt: &str,
    missing: &str,
) -> Result<String, Usage> {
    if value.is_some() && env_name.is_some() && value_explicit {
        return Err(format!("Pass either --{flag} or --{flag}-env, not both.").into());
    }
    if let Some(name) = env_name {
        return match std::env::var(name) {
            Ok(v) if !v.is_empty() => Ok(v),
            _ => Err(format!("--{flag}-env {name}: env var is not set or empty.").into()),
        };
    }
    if let Some(v) = value.filter(|v| !v.is_empty()) {
        return Ok(v.to_owned());
    }
    if std::io::stdin().is_terminal() {
        return rpassword::prompt_password(format!("{prompt}: ")).map_err(|e| Usage(e.to_string()));
    }
    Err(missing.into())
}

fn checks_list(global: &Global) -> Outcome {
    if global.quiet {
        return Ok(ExitCode::SUCCESS);
    }
    let checks = checks::all_checks();
    if global.json {
        #[derive(Serialize)]
        struct Row<'a> {
            name: &'a str,
            description: &'a str,
            order: i32,
            requires: &'a [&'a str],
            kinds: Vec<&'a str>,
        }
        let rows: Vec<Row> = checks
            .iter()
            .map(|c| Row {
                name: c.name,
                description: c.description,
                order: c.order,
                requires: c.requires,
                kinds: c.kind_names(),
            })
            .collect();
        println!(
            "{}",
            jsonfmt::to_pretty_sorted(&rows).expect("rows serialize")
        );
        return Ok(ExitCode::SUCCESS);
    }
    let color = color_stdout();
    println!("Registered checks");
    let mut table = new_table(color);
    table.set_header(["Name", "Kinds", "Order", "Requires", "Description"]);
    for c in checks {
        let requires = if c.requires.is_empty() {
            "-".to_owned()
        } else {
            c.requires.join(", ")
        };
        table.add_row(vec![
            styled(Cell::new(c.name), color, Some(Color::Cyan), false),
            Cell::new(c.kind_names().join(", ")),
            styled(Cell::new(c.order), color, Some(Color::DarkGrey), false),
            styled(Cell::new(requires), color, Some(Color::DarkGrey), false),
            Cell::new(c.description),
        ]);
    }
    println!("{table}");
    Ok(ExitCode::SUCCESS)
}

fn alerts_list(global: &Global) -> Outcome {
    if global.quiet {
        return Ok(ExitCode::SUCCESS);
    }
    let mut types: Vec<_> = ALERTER_TYPES.iter().collect();
    types.sort_by_key(|t| t.name);
    if global.json {
        let rows: Vec<BTreeMap<&str, &str>> = types
            .iter()
            .map(|t| {
                BTreeMap::from([
                    ("name", t.name),
                    ("description", t.description),
                    ("toml_example", t.toml_example),
                ])
            })
            .collect();
        println!(
            "{}",
            jsonfmt::to_pretty_sorted(&rows).expect("rows serialize")
        );
        return Ok(ExitCode::SUCCESS);
    }
    let color = color_stdout();
    for (i, t) in types.iter().enumerate() {
        if i > 0 {
            println!();
        }
        let name = if color {
            format!("\x1b[1;36m{}\x1b[0m", t.name)
        } else {
            t.name.to_owned()
        };
        println!("{name} \u{2014} {}", t.description);
        println!();
        for line in t.toml_example.trim_end().lines() {
            println!("    {line}");
        }
    }
    Ok(ExitCode::SUCCESS)
}

#[derive(Serialize)]
struct AlertTestResult {
    alerter: String,
    ok: bool,
    error: Option<String>,
}

#[derive(Serialize)]
struct AlertTestReport {
    ok: bool,
    results: Vec<AlertTestResult>,
}

fn alerts_test(
    global: &Global,
    name: Option<String>,
    kind: TestKind,
    config: Option<PathBuf>,
) -> Outcome {
    let path = config_path(config);
    if !path.exists() {
        return Err(format!(
            "No config at {}. Provide --config <path> or create a ./{DEFAULT_CONFIG_FILENAME}.",
            path.display()
        )
        .into());
    }
    let cfg = load_or_fail(&path)?;
    let Some(alerts_cfg) = &cfg.alerts else {
        return Err(format!("{} has no [alerts.*] section.", path.display()).into());
    };
    let mut alerters: Vec<Alerter> = build_alerters(alerts_cfg)?;
    if alerters.is_empty() {
        return Err("No alerters configured.".into());
    }
    if let Some(wanted) = &name {
        let mut configured: Vec<&str> = alerters.iter().map(|a| a.name).collect();
        configured.sort_unstable();
        let configured = configured.join(", ");
        alerters.retain(|a| a.name == wanted);
        if alerters.is_empty() {
            return Err(format!(
                "No configured alerter named '{wanted}'. Configured: {configured}."
            )
            .into());
        }
    }

    let now = Utc::now();
    let synthetic =
        |kind: TransitionKind, previous: Status, current: Status, label: &str| Transition {
            kind,
            target_name: "test".into(),
            target_display_name: None,
            target_url: "https://test.example.com".into(),
            check_name: "alert-test".into(),
            previous_status: previous,
            current_status: current,
            message: format!("Test alert from `chap-checker alerts test --kind {label}`."),
            duration_ms: 0.0,
            occurred_at: now,
        };
    let failure = synthetic(TransitionKind::Failure, Status::Ok, Status::Fail, "failure");
    let recovery = synthetic(
        TransitionKind::Recovery,
        Status::Fail,
        Status::Ok,
        "recovery",
    );
    let transitions = match kind {
        TestKind::Failure => vec![failure],
        TestKind::Recovery => vec![recovery],
        TestKind::Both => vec![failure, recovery],
    };

    let chatter = !global.quiet && !global.json;
    if chatter {
        let label = match kind {
            TestKind::Failure => "OK -> FAIL",
            TestKind::Recovery => "FAIL -> OK",
            TestKind::Both => "OK -> FAIL + FAIL -> OK (both directions)",
        };
        let word = if alerters.len() == 1 {
            "alerter"
        } else {
            "alerters"
        };
        println!("Sending synthetic {label} via {} {word}.", alerters.len());
    }

    let rt = runtime();
    let results: Vec<AlertTestResult> = rt.block_on(async {
        let mut out = Vec::new();
        for alerter in &alerters {
            let outcome = alerter.notify(&transitions).await;
            if let Err(e) = &outcome {
                tracing::error!("alerter {} failed: {e}", alerter.name);
            }
            out.push(AlertTestResult {
                alerter: alerter.name.to_owned(),
                ok: outcome.is_ok(),
                error: outcome.err(),
            });
        }
        out
    });
    let report = AlertTestReport {
        ok: results.iter().all(|r| r.ok),
        results,
    };

    if global.json && !global.quiet {
        println!(
            "{}",
            serde_json::to_string_pretty(&report).expect("report serializes")
        );
    } else if chatter {
        let color = color_stdout();
        let mut table = new_table(color);
        table.set_header(["Alerter", "Result", "Detail"]);
        for r in &report.results {
            let (result, fg, detail) = if r.ok {
                (
                    "OK",
                    Color::Green,
                    format!("{} transition(s) delivered", transitions.len()),
                )
            } else {
                (
                    "FAIL",
                    Color::Red,
                    r.error.clone().unwrap_or_else(|| "(no detail)".into()),
                )
            };
            table.add_row(vec![
                styled(Cell::new(&r.alerter), color, Some(Color::Cyan), false),
                styled(Cell::new(result), color, Some(fg), false),
                Cell::new(detail),
            ]);
        }
        println!("{table}");
        if !report.ok {
            println!("One or more alerters failed - rotate creds or check the receiver.");
        }
    }
    Ok(if report.ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    })
}

fn color_stdout() -> bool {
    std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none()
}
