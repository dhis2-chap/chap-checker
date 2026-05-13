# Configuration

`chap-checker` looks for `./chap-checker.toml` in the current working directory
by default. Override the path with `--config <path>` on any subcommand, or set
the `CHAP_CHECKER_CONFIG` env var.

There is **no XDG / system-wide lookup**: the config lives next to wherever
you run `chap-checker` from. Put a copy alongside your cron entry,
ansible-managed deploy, or systemd unit.

## File shape

```toml
# Top-level (optional)
concurrency = 5                     # how many instances to check in parallel

# One [instances.<name>] block per DHIS2 server to monitor.
[instances.prod]
url = "https://dhis2.example.com"
username = "ops"
password = "REPLACE_ME"            # OR set password_env, not both
# password_env = "PROD_PASS"
# timeout_s = 10.0                 # default 10s, must be > 0
# verify_tls = true                # default true
# checks = ["dhis2_ping", ...]     # subset; omit to run every registered check
# alerts = ["slack"]               # opt-in per alerter; omit / [] = no alerts

# One [alerts.<name>] block per transport (only slack today).
[alerts.slack]
webhook_url_env = "SLACK_WEBHOOK_URL"
# webhook_url = "https://hooks.slack.com/services/..."   # alternative
# notify_on = ["fail", "error", "warn"]                  # default
# timeout_s = 10.0
```

A ready-to-edit template lives at
[`chap-checker.toml.example`](https://github.com/dhis2-chap/chap-checker/blob/main/chap-checker.toml.example)
in the repo.

## Credentials

The file carries passwords and webhook URLs — treat it like any other secret.

- Keep it out of git. `chap-checker.toml` is in the repo's `.gitignore`.
- `chmod 600 chap-checker.toml`. The config loader logs a warning on POSIX
  if the file is group- or world-readable while holding any inline `password`
  or `webhook_url`.
- Prefer the `*_env` variants (`password_env`, `webhook_url_env`) and keep the
  actual secret in your shell, secrets manager, or systemd `EnvironmentFile`.

## Per-instance check filter

Add `checks = [...]` on an instance to restrict which checks fire against it.
Transitive `requires` are pulled in automatically, so a partial selection still
has every prerequisite present.

```toml
# DHIS2-only instance (no chap stack):
[instances.pure-dhis2]
url = "https://dhis2.example.com"
username = "admin"
password_env = "DHIS2_PASS"
checks = ["dhis2_ping", "dhis2_system_info"]

# chap stack but no modeling-app check:
[instances.api-only]
url = "https://dhis2.example.com"
username = "admin"
password_env = "DHIS2_PASS"
checks = ["dhis2_chap_system_info"]   # pulls in dhis2_ping, dhis2_chap_route, dhis2_chap_ping
```

Unknown names are rejected at config load. There is no "exclude" syntax — list
the checks you want.

See [Built-in checks](checks.md) for the names and dependency graph.

## Per-instance alert opt-in

`alerts = ["slack", ...]` on an instance lists which configured alerters fire
for that instance. The default is `[]`, so a fresh instance is silent until
you explicitly opt it in. Useful for "ten staging instances, only prod
should page."

```toml
[alerts.slack]
webhook_url_env = "SLACK_WEBHOOK_URL"

[instances.prod]               # pages on transitions
url = "https://dhis2.example.com"
alerts = ["slack"]
# ...

[instances.staging]            # silent, just tracked
url = "https://staging.dhis2.example.com"
# no `alerts =` line, default is []
# ...
```

See [Alerting](alerts.md) for the full transport setup and transition
semantics.

## Concurrency

`concurrency` (default 5) controls how many instances are probed in parallel.
Each target runs its own `httpx.AsyncClient`, so there's no shared HTTP state.
Bump it up for many same-network targets, down to `1` to force serial
execution. Hard upper bound 100.

CLI flag `--concurrency N` on `verify` overrides the config value for one run.
