# chap-checker

CLI that runs a suite of checks against a DHIS2 server with `chap-core` and the
`chap` route installed.

## Usage

Ad-hoc against a single instance:

```bash
chap-checker verify \
    --url https://play.dhis2.org/40.0.0 \
    --username admin \
    --password district
```

Or define multiple instances in `./chap-checker.toml` (see
`chap-checker.toml.example`) and run them all:

```bash
chap-checker verify                       # every [instances.*] in ./chap-checker.toml
chap-checker verify --instance play       # just one
chap-checker verify --config /etc/chap-checker.toml
```

Global flags:

- `--debug` - verbose logging to stderr.
- `--json` - emit machine-readable JSON instead of a Rich table (cron-friendly).
- `--quiet` / `-q` - suppress stdout entirely; just run checks, dispatch
  alerts, and exit (intended for cron).

Exit code is non-zero when any check on any target is not `OK`, so cron picks
up failures naturally.

## Alerting

Add an `[alerts.slack]` section to `./chap-checker.toml` (see
`chap-checker.toml.example`) and `chap-checker` will POST a Slack message
when any check's status flips between OK and a non-OK family member.

Alerting is **stateful**: the previous status of every `(instance, check)`
pair is persisted to `./chap-checker.state.json` (override with `--state
<path>`), so a sustained outage produces one alert on entry and one on
recovery, not a Slack message every cron tick.

Verify the wiring without breaking a real instance:

```bash
chap-checker alert test
```

Skip dispatch on a specific run with `--no-alerts`. The cron-canonical
command is:

```cron
*/15 * * * * chap-checker --quiet verify \
    >> /var/log/chap-checker/runs.log 2>&1
```

## Development

```bash
make install
make lint
make test
```
