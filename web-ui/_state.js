/* eslint-disable no-undef */
/**
 * Wiring layer between the FastAPI ``/api/state`` endpoint and the
 * Claude Designer artifact in ``src/``.
 *
 * The artifact is replaced wholesale on every design-zip drop. This file
 * survives because:
 *
 *  - It's prefixed with ``_`` to mark it as "not from the designer",
 *  - It loads BEFORE the artifact in ``index.html``,
 *  - It exposes ``window.CK_useLiveState(refreshSec)`` — a tiny React
 *    hook the artifact's ``app.jsx`` consumes to pull live instance
 *    data in the shape it expects.
 *
 * The mapping from the server's ``DashboardState`` schema to the
 * artifact's ``INSTANCES_BASE`` shape lives at the bottom of this file
 * so updating either side stays a one-file edit.
 */

(function () {
  "use strict";

  /**
   * Map server "worst_status" (Status enum value) onto the artifact's
   * three-state badge: ok / warn / down.
   * SKIPPED is treated as warn so the operator sees something is off.
   */
  function mapStatus(serverStatus) {
    if (serverStatus === "ok") return "ok";
    if (serverStatus === "warn" || serverStatus === "skipped") return "warn";
    return "down"; // fail, error
  }

  /**
   * Map one server tile -> one artifact instance.
   * Designer artifact expects: id, name, platform, version, url, status,
   * checksPassed/Total, pingPassed/Total/Pct, latency, updated, uptime,
   * checks:[{name,ok,down?}].
   */
  function mapTile(tile) {
    const status = mapStatus(tile.worst_status);
    const checks = (tile.checks || []).map((c) => ({
      name: c.name,
      ok: c.status === "ok",
      down: status === "down",
    }));
    return {
      id: tile.name,
      name: tile.name.toUpperCase(),
      platform: "DHIS2",
      version: tile.version || "—",
      url: tile.url,
      status,
      checksPassed: tile.ok_count,
      checksTotal: tile.total_count,
      pingPassed: tile.ping_ok,
      pingTotal: tile.ping_total,
      pingPct: tile.ping_total > 0
        ? Math.round((100 * tile.ping_ok) / tile.ping_total)
        : 100,
      latency: typeof tile.latency_ms === "number" ? tile.latency_ms : 0,
      updated: "—", // app.jsx recomputes this from lastRefreshAt
      uptime: typeof tile.uptime_pct === "number" ? tile.uptime_pct : 100,
      checks,
    };
  }

  /**
   * React hook used by app.jsx. Polls /api/state at ``pollSec`` cadence and
   * returns the latest mapped instance list. Returns ``null`` until the
   * first response lands so the artifact can fall back to its mock data
   * during the initial mount paint.
   *
   * @param {number} pollSec How often to fetch /api/state.
   * @returns {{instances: Array, lastRefreshAt: number, alertsOn: boolean,
   *           refreshSec: number} | null}
   */
  function useLiveState(pollSec) {
    const [state, setState] = React.useState(null);

    React.useEffect(() => {
      let cancelled = false;

      async function pull() {
        try {
          const r = await fetch("/api/state", { cache: "no-store" });
          if (!r.ok) return;
          const data = await r.json();
          if (cancelled) return;
          setState({
            instances: (data.tiles || []).map(mapTile),
            lastRefreshAt: data.last_refresh
              ? Date.parse(data.last_refresh)
              : Date.now(),
            alertsOn: !!data.alerts_enabled,
            refreshSec: data.interval_s || pollSec,
          });
        } catch (e) {
          // Transient network blip - try again on the next tick.
        }
      }

      pull();
      const id = setInterval(pull, pollSec * 1000);
      return () => {
        cancelled = true;
        clearInterval(id);
      };
    }, [pollSec]);

    return state;
  }

  window.CK_useLiveState = useLiveState;
})();
