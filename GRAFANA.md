# chap-checker and Grafana / Prometheus

This document weighs two questions that have come up more than once:

1. Could chap-checker be **replaced** by a Grafana / Prometheus stack?
2. Could chap-checker be **augmented** to feed a Grafana / Prometheus stack — i.e. keep the TUI and config story, but also expose `/metrics`?

The answer to both is "yes, with caveats." The rest of this file lays out which capabilities map cleanly, which don't, and where the two approaches sit relative to each other. The part that matters most for the second question — the app-presence checks that scan `/api/apps` for the modeling and climate apps — gets its own section, because it's where off-the-shelf exporters strain hardest.

## State of the Grafana ecosystem (as of May 2026)

The Grafana side has consolidated meaningfully since the early "Prometheus + Blackbox + JSON exporter + Alertmanager + Grafana" mental model:

- **Grafana Alloy** is the supported collector. Grafana Agent reached end-of-life on 1 Nov 2025; Promtail moves to EOL Feb 2026. Both are replaced by Alloy, a Grafana-flavoured distribution of the OpenTelemetry Collector. Crucially, Alloy embeds Blackbox Exporter as a built-in component (`prometheus.exporter.blackbox`), so you do not run Blackbox as a separate service any more.
- **Grafana Cloud Synthetic Monitoring** is powered by k6 and supports two check types beyond the classic Blackbox-style HTTP probe: **MultiHTTP** (a chain of requests with extracted variables) and **k6 Scripted** (full JavaScript). Scripted checks can fetch authenticated JSON endpoints, parse responses, iterate arrays, do UUID matching with arbitrary key tolerance, and emit custom Prometheus metrics. This is the option that materially changes the calculus for the app-presence checks below.
- **Grafana Mimir** is the horizontally-scalable, multi-tenant Prometheus-compatible long-term store. Single-tenant single-server deployments do not need it.
- **JSON Exporter** is still a community project (`prometheus-community/json_exporter` v0.7.0, Feb 2025) using the Kubernetes JSONPath engine. Third-party forks (`peak/prometheus-json-exporter`, `utilitywarehouse/json_exporter`) use `jq` instead and have richer filter expressions. The k6 path makes both moot for anything non-trivial.

Net effect: the "4+ services" claim that historically applied to a Grafana stack overstates the operational footprint of a 2026 deployment. With Grafana Cloud + Alloy, you can run with one self-hosted process (Alloy) and zero self-hosted backend services.

## What chap-checker does today

Seven authenticated checks per DHIS2 instance, with a parent/child skip chain so dependent checks are silenced when a prerequisite fails. Transition-only alerts (OK ↔ non-OK flips) dispatched via Slack or generic webhook, deduped through a JSON state file. A Textual TUI and a browser dashboard render the live tile grid; both consume the same `DashboardServer.snapshot()` at `src/chap_checker/daemon.py:143`. Single-file config (`chap-checker.toml`), single-process operator UX (`uvx chap-checker run`).

The check chain (so the rest of this doc can refer to it):

- `dhis2_ping` (parent — auth probe to `/api/me`)
  - `dhis2_system_info` (`/api/system/info`, extracts DHIS2 version)
  - `dhis2_chap_route` (`/api/routes?filter=code:eq:chap`)
    - `dhis2_chap_ping` (`/api/routes/chap/run/health` proxied to chap-core)
    - `dhis2_chap_system_info` (chap-core version through the same proxy)
    - `dhis2_chap_modeling_app` (scan `/api/apps`, match by UUID)
    - `dhis2_chap_climate_app` (scan `/api/apps`, match by UUID)

## Capability mapping

| chap-checker capability | Grafana-stack equivalent | Fit |
|---|---|---|
| `dhis2_ping` — auth probe to `/api/me`, expects JSON with `username` | Alloy's `prometheus.exporter.blackbox` component with basic auth + `fail_if_body_not_matches_regexp` | Clean |
| `dhis2_chap_ping` — proxied `/api/routes/chap/run/health` | Same — blackbox probe with auth, embedded in Alloy | Clean |
| `dhis2_system_info` — extract DHIS2 version string | k6 scripted check (parse JSON, emit version as a metric label); or JSON Exporter with JSONPath `$.version` if you prefer the classic stack | Clean with k6, Medium with JSON Exporter |
| `dhis2_chap_system_info` — chap-core version through DHIS2 proxy | Same — k6 scripted check trivial, JSON Exporter pattern workable | Clean with k6, Medium with JSON Exporter |
| `dhis2_chap_route` — filter `/api/routes` by `code=chap`, check `disabled` flag | k6 scripted check (filter in JS, assert `disabled === false`); JSON Exporter with JSONPath is workable but ugly | Clean with k6, Medium with JSON Exporter |
| **`dhis2_chap_modeling_app` / `dhis2_chap_climate_app`** — match `/api/apps` by `app_hub_id` UUID, tolerate snake_case ↔ camelCase, extract version | k6 scripted check: `apps.find(a => (a.app_hub_id ?? a.appHubId) === UUID)` and emit `version` as a custom metric. JSON Exporter alternative: two modules per app (one per key style) merged with a recording rule | Clean with k6, Awkward with JSON Exporter — see next section |
| Parent/child skip chain | Alertmanager `inhibit_rules` to suppress *alerts* when the parent alert fires — but the probes still run, the dashboard still shows N red cells. k6 scripted checks can express the chain in JS but each top-level check is independent in the SM scheduler | Awkward — semantic loss |
| Transition-only alerts + dedup + retry on delivery failure | Alertmanager (or Grafana-managed alerting in Grafana Cloud) handles this natively, with grouping / silencing / routing | Wins for Grafana |
| Slack + generic webhook transports | Alertmanager has both, plus PagerDuty, OpsGenie, MS Teams, etc. | Wins for Grafana |
| Per-tile rolling 30-refresh history sparkline | Prometheus / Mimir TSDB + Grafana panel; real history retention | Wins for Grafana |
| Uptime %, latency mean | Standard PromQL (`rate`, `avg_over_time`); gains percentiles, longer windows | Wins for Grafana |
| Textual TUI | No equivalent — Grafana is browser-only | Loss |
| `chap-checker.toml` + `uvx chap-checker run` operator UX | Self-hosted: Alloy + Prometheus/Mimir + Grafana (3 services). Grafana Cloud: Alloy locally + everything else hosted (1 self-hosted service). Either way still meaningfully more setup than a single Python process | Loss — but smaller gap than the legacy "4+ services" framing suggested |

## The app-presence checks

The two app checks (`dhis2_chap_modeling_app`, `dhis2_chap_climate_app`) used to be the part of chap-checker that bent generic monitoring tools out of shape. With the 2026 Grafana stack — specifically k6 scripted checks in Synthetic Monitoring — they map cleanly. The picture differs sharply between the classic-exporter shape and the k6 shape, so both are worth showing.

**Why Blackbox Exporter alone can't do it.** `/api/apps` returns a JSON *array*, not a single object. Identifying "is the modeling app installed?" means filtering by `app_hub_id` UUID, then asserting presence plus a populated `version` field. Blackbox has no JSONPath; the closest it can do is a body-regex for the UUID string, which is fragile (substring match, no structural awareness, no version extraction).

**Classic stack: JSON Exporter, with leaks.** The Prometheus JSON Exporter (v0.7.0, Feb 2025) supports JSONPath via the Kubernetes JSONPath engine, so a per-app module can in principle do the match:

```yaml
modules:
  dhis2_apps:
    metrics:
      - name: dhis2_app_installed
        path: '{$[?(@.app_hub_id=="a29851f9-82a7-4ecd-8b2c-58e0f220bc75")]}'
        type: object
        labels:
          app: modeling
        values:
          version: '{.version}'
```

Two leaks:

- (a) JSONPath filter expressions in the Kubernetes engine can't OR across keys. Some DHIS2 versions return `app_hub_id` (snake_case), others return `appHubId` (camelCase). chap-checker handles this in Python with a pydantic alias (`src/chap_checker/checks/dhis2_chap_modeling_app.py:34` — `Field(default=None, alias="appHubId")` plus `populate_by_name`). With the upstream JSON Exporter you need *two* modules per app and a recording rule that ORs them. There are `jq`-based community forks (`peak/prometheus-json-exporter`, `utilitywarehouse/json_exporter`) that can do the OR in one expression — but you take on a non-upstream dependency.
- (b) The `version` value lands as a string label, which is fine for display but makes alerting on "version is too old" awkward. You'd need a relabel rule with a regex per supported version.

**Modern stack: k6 scripted check, clean.** Grafana Cloud Synthetic Monitoring's k6 scripted check is just JavaScript with a built-in HTTP client and a custom-metrics API. The same logic chap-checker runs in Python collapses to a few lines:

```javascript
import http from 'k6/http';
import { Counter } from 'k6/metrics';
const modelingFound = new Counter('dhis2_app_modeling_found');
const modelingVersion = new Counter('dhis2_app_modeling_version_info');

export default function () {
  const r = http.get(`${__ENV.DHIS2_URL}/api/apps`, { headers: { Authorization: `Basic ${__ENV.BASIC}` } });
  const apps = r.json();
  const match = apps.find(a => (a.app_hub_id ?? a.appHubId) === 'a29851f9-82a7-4ecd-8b2c-58e0f220bc75');
  modelingFound.add(match ? 1 : 0);
  if (match?.version) modelingVersion.add(1, { version: match.version });
}
```

The snake/camel OR, the version extraction, the typed result — all in one place, in real JavaScript, with the same test ergonomics chap-checker has in Python. This is the option that closes the gap.

**What chap-checker does today** (for reference). One Python function per app UUID that parses the response (with the pydantic alias above), walks the list, returns a typed `Status` (OK / WARN / FAIL) and a human-readable message — "modeling app installed, version 1.4.2" on the green path, "No app with app_hub_id 'a29851f9-...' installed." on the red path. All seven checks share the same shape; adding a third app is a one-file change.

**Bottom line.** If you go the classic Blackbox + JSON Exporter route, the app-presence checks are the weakest link, and Path B (augmentation) is the strongest answer. If you go the k6 / Synthetic Monitoring route, the gap closes; the case for Path B is then about operator UX (the TUI, single config) rather than capability.

## Path A — full replacement

Two realistic shapes — pick by how much you're willing to host yourself.

**A1. Modern / minimal-ops: Grafana Cloud + Alloy.**

- Grafana Cloud account (free tier covers small fleets). Provides Prometheus-compatible metrics, Grafana, Alertmanager-equivalent ("Grafana-managed alerting"), and Synthetic Monitoring.
- A single Alloy process per environment (optional — only if you also want infra metrics). Synthetic Monitoring checks run from Grafana's probes; for internal-only DHIS2 instances, deploy the private probe (also Alloy-based).
- A k6 scripted check per logical chap-checker check, or one consolidated script per target. Each emits the relevant custom metrics. The script captures the parent/child semantics in plain JavaScript (early `return` if the auth probe fails) instead of trying to encode it in alert inhibition.
- Grafana-managed alerts routed to Slack / webhook, with inhibition rules for the cross-check skip semantics that don't fit inside a single script.
- Grafana dashboard with stat panels per instance, versions as labels.

**A2. Classic / fully self-hosted.**

- Alloy (embeds Blackbox Exporter via `prometheus.exporter.blackbox`), plus a separate JSON Exporter for the JSON-extraction checks.
- Prometheus single-binary (or Grafana Mimir if you have multi-tenant or long-retention requirements — see "Backend recommendation" below).
- Alertmanager for routing + inhibition rules.
- Grafana for dashboards.

A1 absorbs the operational complexity onto Grafana Cloud and lets k6 scripts handle the awkward JSON shapes cleanly. A2 keeps everything on-prem at the cost of running and tuning four-or-so services and accepting the JSON Exporter awkwardness from the previous section.

What you gain:

- Real historical retention (TSDB instead of a 30-element in-memory deque).
- Ad-hoc PromQL; percentile latency; longer SLA windows.
- Standard alert routing — PagerDuty, OpsGenie, MS Teams, on-call rotations — without writing new transports.
- Multi-tenant dashboards in orgs that already run Grafana.
- One less in-house tool to maintain.

What you lose:

- The TUI. Not portable to Grafana.
- The one-config-file install. Even at the minimum — Grafana Cloud + a private Alloy probe — you have an account, an Alloy config, and a synthetics check definition instead of a single TOML.
- The parent/child skip semantics. You get downstream *alert* suppression via Alertmanager inhibition, but the Grafana dashboard still lights up red across the board when DHIS2 itself is down — instead of "ping failed, dependents skipped."
- The operator UX where you `uvx chap-checker run` immediately after an install and watch tiles go green.

Honest verdict: A1 (Grafana Cloud + Alloy + k6 scripted checks) is genuinely viable today in a way the 2023-era stack wasn't — k6 scripted checks resolve the app-presence awkwardness, and there's no on-prem TSDB to operate. A2 stays roughly as painful as before. Neither shape is right for the "I just finished installing DHIS2, did it work?" moment, which is what chap-checker's TUI is for.

## Path B — augmentation (chap-checker as a Prometheus exporter)

Shape:

- Add a `chap-checker exporter` subcommand that runs the existing checks on a timer and exposes them as Prometheus text format at `/metrics`. Reuses `run_targets()` from `src/chap_checker/runner.py` verbatim — no second implementation of the check logic.
- Each check becomes a labeled gauge:
  ```
  dhis2_check_status{target="prod", check="dhis2_chap_modeling_app"} 0
  ```
  (0 = OK, 1 = WARN, 2 = FAIL, 3 = ERROR, 4 = SKIPPED — same enum the TUI already uses.)
- Per-target context exposed as an info-style metric, in the style of `node_exporter`'s `node_uname_info`:
  ```
  dhis2_info{target="prod", dhis2_version="2.42.1", modeling_app_version="1.4.2", climate_app_version="2.1.0"} 1
  ```
- Alerts handled by Alertmanager. chap-checker's own alert dispatch is disabled in this mode (or kept on as a redundant channel — operator choice).

What you gain:

- Existing chap-checker users keep the TUI, single-file config, and quick-install story.
- Ops teams running Grafana plug it in like any other exporter — one scrape job, one dashboard import.
- The DHIS2-specific knowledge (app UUID matching, snake/camel key tolerance, route filter, version extraction, parent/child skip chain) stays in one place — the Python checks — instead of being scattered across YAML files across multiple exporters.
- The app-presence check works correctly the first time, including the snake/camel case, because it's the same code path the TUI already exercises.

What you lose:

- Nothing in the current tool. This is purely additive — an output format, not a replacement.

Honest verdict: this is the high-leverage move if Grafana support becomes a real ask. Probably on the order of 100 lines of code (a FastAPI route or a `prometheus_client` generator over the existing `RunReport` shape) plus docs.

## Self-hosted vs Grafana Cloud

The hosting decision is independent from the Path A / Path B decision but interacts with it. The honest framing is: for chap-checker's typical audience (a health ministry or NGO with one or a handful of DHIS2 instances and limited platform-engineering capacity), the trade-off is rarely "which is cheaper" — both can be free at this scale. It's about data residency, network topology, and who's on the hook when something breaks.

| Concern | Grafana Cloud | Self-hosted |
|---|---|---|
| **Cost at small scale** | Free tier (10k series, 14-day metrics retention, 3 users) covers a single-org DHIS2 fleet with comfortable headroom. Paid tiers from ~$8/user/mo. | Software is free; operational cost is whatever a sysadmin's time costs. Two-or-three VMs minimum. |
| **Operational burden** | Almost none. Grafana Labs operates the TSDB, the alerting stack, and the UI. Upgrades happen for you. | You operate Prometheus/Mimir/VictoriaMetrics, Grafana itself, Alertmanager, and patch them. Backups, disk pressure, retention pruning are your problem. |
| **Data residency** | Metric data + scraped response bodies leave the organisation's network. Grafana Cloud regions are EU / US / AU / etc., but not (e.g.) a specific African country. For health ministries with PII / sovereignty constraints, this is often the dealbreaker — even though check metrics typically don't contain PII, the URLs being probed do reveal which DHIS2 instances exist. | Everything stays on your network. The default for most ministry-of-health style deployments. |
| **Network topology — public DHIS2** | Grafana's global probes (public probe network) can hit a public DHIS2 directly. Zero setup beyond pasting the URL. | Same as today — your monitoring host needs to reach DHIS2. |
| **Network topology — internal DHIS2** | Requires deploying a **private probe** (an Alloy process inside your network). That probe phones home to Grafana Cloud and runs the synthetic checks against internal targets. One process to operate, but bridges the air gap cleanly. | No bridging needed — monitoring and target both inside your network. |
| **Firewall posture** | Requires outbound HTTPS from inside the network to `*.grafana.net`. Some health-ministry networks have outbound-deny defaults; getting an exception added can take longer than it sounds. | All traffic stays internal. |
| **Multi-tenant (e.g. monitoring DHIS2 for several countries from a regional hub)** | Built in — Grafana Cloud orgs map cleanly to tenants. Native multi-tenancy without standing up Mimir. | Possible with Mimir (`X-Scope-OrgID` header) but you operate the Mimir cluster. Significant lift. |
| **Lock-in / pricing risk** | Real but bounded — Prometheus-compatible APIs mean migration off Cloud is feasible if pricing changes. Still, your dashboards, alerts, and synthetic check definitions are Grafana-Cloud-shaped. | None — open-source the whole way down. |
| **Synthetic Monitoring (k6 scripted checks)** | First-class. This is the thing that resolves the app-presence check awkwardness in Path A. | k6 can run self-hosted (open-source `xk6` and `k6 run` binary) but you operate the scheduler and metric pipeline yourself. Doable; meaningfully more work than the Cloud version. |
| **Alerting maturity** | Grafana-managed alerting; routes to Slack, PagerDuty, OpsGenie, MS Teams, webhook, email out of the box. | Alertmanager — same destinations, you configure the routing tree yourself. |

**Concrete framing for the chap-checker audience:**

- **Most DHIS2 deployments → Grafana Cloud free tier** if data residency allows it. The free tier covers a single-org fleet; the operational burden saving is the biggest single win for teams without a dedicated SRE.
- **Health-ministry deployments with data-sovereignty constraints → self-hosted, single-node Prometheus + Grafana.** Don't reach for Mimir until multi-tenancy is a real requirement.
- **A regional hub monitoring DHIS2 instances across several countries → Grafana Cloud (one org per country) or self-hosted Mimir with `X-Scope-OrgID` per country.** Pick by whether the regional hub is willing to host metrics in Grafana Cloud.

The combination that's hardest to justify: self-hosting the *entire* stack just to monitor one DHIS2 instance. At that scale, Path B (chap-checker exporter into a tiny Prometheus + Grafana) beats Path A on effort-per-outcome — or you stick with chap-checker as-is.

## Backend recommendation (if self-hosting)

The time-series backend choice only matters in the self-hosted column above; Grafana Cloud abstracts it away. For self-hosted chap-checker users:

- **Single-node Prometheus + Grafana — recommended default.** One binary for the TSDB, one for the dashboard, runs comfortably on modest hardware. Easy to operate. Covers up to "a few dozen targets, weeks of retention" without strain. This is the right answer for a single ministry / single org.
- **VictoriaMetrics — recommended if Prometheus's RAM / disk footprint is a problem.** Prometheus-compatible ingestion and PromQL; single binary; significantly less RAM and disk per series. Good fit if you want to keep months of retention on small hardware.
- **Grafana Mimir — only if multi-tenant or genuine horizontal-scale is required.** Mimir exists for "platform team providing metrics-as-a-service to many engineering teams" — multi-tenancy via `X-Scope-OrgID`, object-storage long-term retention, horizontal scaling. Overkill for one organisation monitoring its own DHIS2 instances; appropriate for a regional hub monitoring many.

## Recommendation

**Don't replace.** The capability gap that used to exist (app-presence checks fighting JSONPath) closes with the 2026 stack — k6 scripted checks make the JSON shape problems disappear. But the *operator UX* gap doesn't close: chap-checker is one Python process, one TOML, one `uvx chap-checker run` away from a terminal tile grid; the equivalent Grafana setup is at minimum an account or three services. Replacing chap-checker still costs the post-install-verification experience for an audience (DHIS2 admins) that disproportionately benefits from it.

**Augment if asked.** Adding a `chap-checker exporter` subcommand that exposes `/metrics` slots the existing check logic into any Grafana deployment with low risk and no duplication. The DHIS2-specific bits (app UUID matching, route filter, version extraction) stay in Python where they're testable and version-aware, instead of being re-implemented in exporter YAML. If Grafana support shows up on the roadmap, this is the path.
