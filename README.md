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

Exit code is non-zero when any check on any target is not `OK`, so cron picks
up failures naturally.

## Development

```bash
make install
make lint
make test
```
