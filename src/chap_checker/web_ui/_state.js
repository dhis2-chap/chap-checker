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
 *
 * Bearer-token auth (0.7+): when the server's `[auth]` block is set,
 * `/api/state` returns 401 without an Authorization header. The SPA
 * stores the operator-typed token in localStorage (key
 * ``CK_TOKEN_KEY``) and attaches it on every fetch. ``CK_AUTH_STATE``
 * (a tiny event-emitter) lets the artifact's login modal cooperate
 * with this hook without prop-drilling.
 */

(function () {
  "use strict";

  const CK_TOKEN_KEY = "chap_checker_token";

  /**
   * Read the stored bearer token (or "" if none). Falls back to "" if
   * localStorage is unavailable (private-mode iOS, file:// origins).
   */
  function readToken() {
    try {
      return window.localStorage.getItem(CK_TOKEN_KEY) || "";
    } catch (_e) {
      return "";
    }
  }

  function writeToken(value) {
    try {
      if (value) {
        window.localStorage.setItem(CK_TOKEN_KEY, value);
      } else {
        window.localStorage.removeItem(CK_TOKEN_KEY);
      }
    } catch (_e) {
      // ignore - the modal handles empty-token failures via the 401 retry
    }
  }

  /**
   * Build an Authorization header dict if a token exists, else `{}`.
   * Always pass `cache: 'no-store'` alongside.
   */
  function authHeaders() {
    const t = readToken();
    return t ? { Authorization: "Bearer " + t } : {};
  }

  /**
   * Tiny event-emitter the artifact's app.jsx subscribes to. Two events:
   *
   *  - "needs-token" - fired when /api/state returns 401. The login modal
   *    listens and renders itself.
   *  - "token-set"   - fired when the modal stores a new token. The hook
   *    listens and triggers an immediate refetch instead of waiting for
   *    the next poll tick.
   */
  const listeners = { "needs-token": [], "token-set": [] };
  function on(evt, fn) {
    if (!listeners[evt]) return () => {};
    listeners[evt].push(fn);
    return () => {
      listeners[evt] = listeners[evt].filter((f) => f !== fn);
    };
  }
  function emit(evt) {
    (listeners[evt] || []).forEach((f) => {
      try {
        f();
      } catch (_e) {
        // swallow; one bad subscriber doesn't blow up the bus
      }
    });
  }

  // Expose both `on` (subscribe) and `emit` (fire) on the public bus so
  // _auth.js can drive an immediate refetch when the user signs in,
  // instead of relying on a wrapper-around-on hack that recorded
  // subscribers in a separate dict (which left useLiveState's `pull`
  // unreachable because it was registered via the closure-captured `on`
  // before any wrapper could intercept it).
  window.CK_AUTH = {
    TOKEN_KEY: CK_TOKEN_KEY,
    readToken,
    writeToken,
    on,
    emit,
    signOut() {
      writeToken("");
      // Reload so cached React state goes away.
      window.location.reload();
    },
  };

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
   * Deterministic integer hash of an instance name. The artifact's
   * ``buildHistory`` uses ``inst.seed * 17 + 1`` as the mulberry32 seed;
   * without a valid integer here the sparkline rng degenerates to all-
   * zero and every history sample shows as a hiccup (red bar). Any
   * non-zero integer fixed per instance works; we use sum-of-chars.
   */
  function hashSeed(name) {
    let h = 0;
    for (let i = 0; i < name.length; i++) {
      h = (h * 31 + name.charCodeAt(i)) | 0;
    }
    return h || 1;
  }

  function mapTile(tile) {
    const status = mapStatus(tile.worst_status);
    const checks = (tile.checks || []).map((c) => ({
      name: c.name,
      ok: c.status === "ok",
      down: status === "down",
    }));
    const latency = typeof tile.latency_ms === "number" ? tile.latency_ms : 0;
    const history = (tile.history || []).map((p) => {
      const s = mapStatus(p.status);
      return {
        status: s,
        ok: s === "ok",
        latency: typeof p.latency_ms === "number" ? p.latency_ms : null,
      };
    });
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
      latency,
      updated: "—",
      uptime: typeof tile.uptime_pct === "number" ? tile.uptime_pct : 100,
      checks,
      history,
      seed: hashSeed(tile.name),
      baseLat: latency || 150,
    };
  }

  /**
   * React hook used by app.jsx. Polls /api/state at ``pollSec`` cadence and
   * returns the latest mapped instance list. Returns ``null`` until the
   * first response lands so the artifact can fall back to its mock data
   * during the initial mount paint.
   *
   * Auth: every fetch carries the stored bearer token as
   * ``Authorization: Bearer <token>``. A 401 response fires the
   * ``needs-token`` event so the login modal can render; the hook
   * doesn't update state on 401 (keeps the last good snapshot). When a
   * fresh token is stored via the modal, the hook listens for
   * ``token-set`` and refetches immediately.
   */
  function useLiveState(pollSec) {
    const [state, setState] = React.useState(null);

    React.useEffect(() => {
      let cancelled = false;

      async function pull() {
        try {
          const r = await fetch("/api/state", {
            cache: "no-store",
            headers: authHeaders(),
          });
          if (r.status === 401) {
            emit("needs-token");
            return;
          }
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
            uiTitle: data.ui_title || "DHIS2 / Climate Instance Checker",
            uiTheme: data.ui_theme || "phosphor",
          });
        } catch (_e) {
          // Transient network blip - try again on the next tick.
        }
      }

      const unsub = on("token-set", pull);
      pull();
      const id = setInterval(pull, pollSec * 1000);
      return () => {
        cancelled = true;
        clearInterval(id);
        unsub();
      };
    }, [pollSec]);

    return state;
  }

  window.CK_useLiveState = useLiveState;
})();
