# Cron deployment

The whole tool is shaped around being driven by cron / a Kubernetes `CronJob` /
systemd timers — anything that runs a command on a schedule and cares about
the exit code.

## Just-signal mode

Smallest possible cron line: quiet stdout, non-zero exit on failure. Combine
with `cron`'s `MAILTO` for trouble notifications, or just rely on your
alerting transport (see [Alerting](alerts.md)).

```cron
*/15 * * * * chap-checker --quiet verify
```

## Structured logs for ingestion

Append a JSON line per run to a log file your aggregator can ingest.

```cron
*/15 * * * * chap-checker --json verify \
    >> /var/log/chap-checker/runs.jsonl \
    2>> /var/log/chap-checker/err.log
```

`--json` writes the [structured report](cli.md#json-output) to stdout, stderr
keeps debug / error lines, and the two redirects keep them in separate files.

## Configuration & state on a server

A typical layout:

```
/etc/chap-checker/
├── chap-checker.toml          # mode 600, owned by the cron user
└── chap-checker.state.json    # mode 600, written by chap-checker on each run

/var/log/chap-checker/
├── runs.jsonl
└── err.log
```

Cron entry:

```cron
*/15 * * * * cd /etc/chap-checker && chap-checker --json verify \
    >> /var/log/chap-checker/runs.jsonl \
    2>> /var/log/chap-checker/err.log
```

Or pass the paths explicitly:

```cron
*/15 * * * * chap-checker --json verify \
    --config /etc/chap-checker/chap-checker.toml \
    --state /etc/chap-checker/chap-checker.state.json \
    >> /var/log/chap-checker/runs.jsonl \
    2>> /var/log/chap-checker/err.log
```

The state file's parent directory is created on first save, so a fresh host
doesn't need explicit setup beyond placing the config.

## Kubernetes CronJob

A minimal sketch — adapt to your secrets handling:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: chap-checker
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: chap-checker
              image: ghcr.io/dhis2-chap/chap-checker:latest
              args: ["--quiet", "verify"]
              env:
                - name: SLACK_WEBHOOK_URL
                  valueFrom:
                    secretKeyRef: { name: chap-checker, key: slack_webhook_url }
                - name: PROD_PASS
                  valueFrom:
                    secretKeyRef: { name: chap-checker, key: prod_password }
              volumeMounts:
                - { name: config, mountPath: /etc/chap-checker }
                - { name: state,  mountPath: /var/lib/chap-checker }
          volumes:
            - name: config
              configMap: { name: chap-checker-config }
            - name: state
              persistentVolumeClaim: { claimName: chap-checker-state }
```

The state PVC matters: alerting is stateful, and a fresh-state pod treats
every entry as a first-time observation, producing a wave of "new failure"
alerts after every pod restart.

## What `alerts test` is NOT for

`alerts test` posts a real Slack message every invocation. Put it behind a
manual run (post-webhook-rotation sanity check), not a cron — daily synthetic
messages are operationally annoying.

## Exit codes

| Code | Meaning                                                                            |
| ---- | ---------------------------------------------------------------------------------- |
| 0    | Every check on every target returned `OK`.                                         |
| 1    | At least one check is not `OK` (FAIL / ERROR / WARN / SKIPPED).                    |
| 2    | Bad CLI usage (unknown flag, unknown check name, missing required arg). Typer default. |

Alert delivery failures **never** affect the exit code — they're logged and
the state save is skipped so the transition is retried next run.
