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
   * Tiny event-emitter the artifact's app.jsx subscribes to. Three events:
   *
   *  - "needs-token" - fired when /api/state returns 401. The login modal
   *    listens and renders itself.
   *  - "token-set"   - fired when the modal stores a new token. The hook
   *    listens and triggers an immediate refetch instead of waiting for
   *    the next poll tick.
   *  - "refresh-now" - fired when the artifact's *Refresh now* command /
   *    `r` keybinding runs. The hook listens and refetches /api/state
   *    immediately, matching what the user sees in the TUI.
   */
  const listeners = { "needs-token": [], "token-set": [], "refresh-now": [] };
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
    // Public helper for any fetch() the artifact issues. Returns the
    // Authorization header dict if a token is stored, else {}. Saves
    // duplicating the localStorage read in app.jsx (which had a bug
    // where POST /api/reload skipped the header entirely, so reload
    // returned 401 on every auth-enabled deployment).
    headers: authHeaders,
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

  /**
   * Parse a server timestamp into epoch milliseconds, or ``null``.
   *
   * Accepts both offset-bearing ISO strings (``2026-11-17T10:00:00+00:00``,
   * ``...Z``) and legacy naive ones (``2026-11-17T10:00:00.123456``), which
   * ``Date.parse`` reads as browser-local time. Python emits microseconds;
   * the fraction is trimmed to milliseconds first because not every engine
   * accepts more than three fractional digits.
   */
  function parseServerTime(value) {
    if (typeof value !== "string" || !value) return null;
    const trimmed = value.replace(/(\.\d{3})\d+/, "$1");
    const ms = Date.parse(trimmed);
    return Number.isFinite(ms) ? ms : null;
  }

  /**
   * Estimate ``serverClock - browserClock`` in ms from the response's
   * ``Date`` header (same-origin, so it is readable). The header only has
   * one-second resolution, so offsets under a few seconds are treated as
   * zero to keep the "Ns ago" counters from jittering.
   */
  function serverClockOffset(dateHeader, receivedAt) {
    const serverNow = dateHeader ? Date.parse(dateHeader) : NaN;
    if (!Number.isFinite(serverNow)) return 0;
    const offset = serverNow - receivedAt;
    return Math.abs(offset) < 3000 ? 0 : offset;
  }

  /** Server timestamp -> browser-clock epoch ms (or null). */
  function toLocalTime(value, offset) {
    const ms = parseServerTime(value);
    return ms === null ? null : ms - offset;
  }

  function mapTile(tile, offset) {
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
      // Optional `name = "..."` from the instance's TOML block. Uppercased to
      // match `name` above; card.jsx falls back to `name` when it is null.
      display_name: tile.display_name ? tile.display_name.toUpperCase() : null,
      // "DHIS2" or "OCS" from the instance kind; older daemons omit it.
      platform: tile.platform || "DHIS2",
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
      // When the daemon last checked this instance, on the browser's clock
      // (null until its first check run). app.jsx renders "updated Ns ago"
      // from this, so the card ages honestly when the data stops moving.
      updatedAt: toLocalTime(tile.last_refresh, offset),
      updated: "—",
      uptime: typeof tile.uptime_pct === "number" ? tile.uptime_pct : 100,
      checks,
      history,
      seed: hashSeed(tile.name),
      baseLat: latency || 150,
    };
  }

  // A poll that takes longer than this counts as a failure, so a daemon
  // that accepts the connection but never answers still surfaces as
  // "connection lost" instead of a request that never ends.
  const FETCH_TIMEOUT_MS = 10000;

  function describeFetchError(err) {
    if (err && err.name === "AbortError") return "request timed out";
    return "network error";
  }

  /**
   * React hook used by app.jsx. Polls /api/state every ``pollSec`` seconds.
   *
   * Never returns mock data. Before the first successful response
   * ``instances`` is ``null`` and app.jsx renders a loading panel. Fields:
   *
   *  - ``instances`` / ``alertsOn`` / ``refreshSec`` / ``uiTitle`` /
   *    ``uiTheme``: the last good snapshot (kept across failed polls).
   *  - ``connection``: "connecting" (no answer yet), "ok" (last poll
   *    succeeded), "error" (last poll failed), "auth" (last poll got 401).
   *  - ``error``: short description of the last failure.
   *  - ``lastSuccessAt``: browser time of the last successful poll.
   *  - ``serverLastRefreshAt``: the daemon's ``last_refresh`` (end of its
   *    last check run), converted to the browser clock.
   *  - ``dataSeenAt``: browser time when ``last_refresh`` last changed, a
   *    clock-independent signal that the daemon's loop is still moving.
   *
   * ``CK_freshness(state, nowMs)`` turns this into the UI's freshness state.
   *
   * Auth: every fetch carries the stored bearer token as
   * ``Authorization: Bearer <token>``. A 401 response fires the
   * ``needs-token`` event so the login modal can render. When a fresh
   * token is stored via the modal, the hook listens for ``token-set`` and
   * refetches immediately.
   *
   * ``options.enabled === false`` turns polling off (explicit demo mode)
   * and makes the hook return ``null``.
   */
  function useLiveState(pollSec, options) {
    const enabled = !(options && options.enabled === false);
    const [state, setState] = React.useState(() => ({
      instances: null,
      alertsOn: false,
      refreshSec: null,
      pollSec,
      uiTitle: null,
      uiTheme: null,
      connection: "connecting",
      error: null,
      lastSuccessAt: null,
      lastFailureAt: null,
      failCount: 0,
      serverLastRefreshAt: null,
      rawLastRefresh: null,
      dataSeenAt: null,
    }));

    React.useEffect(() => {
      if (!enabled) return undefined;
      let cancelled = false;
      let inFlight = false;

      // The unprotected /api/auth hint carries the configured [ui] theme
      // and title, so the loading panel paints in the operator's theme
      // before the first (possibly 401-gated) /api/state answer.
      (async () => {
        try {
          const r = await fetch("/api/auth", { cache: "no-store" });
          if (!r.ok) return;
          const hint = await r.json();
          if (cancelled || !hint) return;
          setState((s) => ({
            ...s,
            uiTheme: s.uiTheme || hint.ui_theme || null,
            uiTitle: s.uiTitle || hint.ui_title || null,
          }));
        } catch (_e) {
          // No hint: the loading panel keeps the default theme.
        }
      })();

      function fail(message) {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          connection: "error",
          error: message,
          lastFailureAt: Date.now(),
          failCount: s.failCount + 1,
        }));
      }

      async function pull() {
        if (inFlight) return;
        inFlight = true;
        const ctrl = typeof AbortController === "function" ? new AbortController() : null;
        const timer = ctrl ? setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS) : null;
        try {
          let r;
          try {
            r = await fetch("/api/state", {
              cache: "no-store",
              headers: authHeaders(),
              signal: ctrl ? ctrl.signal : undefined,
            });
          } catch (err) {
            fail(describeFetchError(err));
            return;
          }
          if (r.status === 401) {
            if (!cancelled) setState((s) => ({ ...s, connection: "auth", error: null }));
            emit("needs-token");
            return;
          }
          if (!r.ok) {
            fail("HTTP " + r.status);
            return;
          }
          let data;
          try {
            data = await r.json();
          } catch (err) {
            fail(err && err.name === "AbortError" ? "request timed out" : "invalid response");
            return;
          }
          if (cancelled) return;
          const receivedAt = Date.now();
          const offset = serverClockOffset(r.headers.get("date"), receivedAt);
          const rawLast = data.last_refresh || null;
          setState((s) => ({
            ...s,
            instances: (data.tiles || []).map((t) => mapTile(t, offset)),
            alertsOn: !!data.alerts_enabled,
            refreshSec: data.interval_s || null,
            uiTitle: data.ui_title || s.uiTitle,
            uiTheme: data.ui_theme || s.uiTheme,
            connection: "ok",
            error: null,
            lastSuccessAt: receivedAt,
            failCount: 0,
            serverLastRefreshAt: toLocalTime(rawLast, offset),
            rawLastRefresh: rawLast,
            // Keep the first-seen time while last_refresh is unchanged.
            dataSeenAt:
              s.lastSuccessAt !== null && s.rawLastRefresh === rawLast && s.dataSeenAt !== null
                ? s.dataSeenAt
                : receivedAt,
          }));
        } finally {
          if (timer) clearTimeout(timer);
          inFlight = false;
        }
      }

      const unsubToken = on("token-set", pull);
      const unsubRefresh = on("refresh-now", pull);
      pull();
      const id = setInterval(pull, pollSec * 1000);
      return () => {
        cancelled = true;
        clearInterval(id);
        unsubToken();
        unsubRefresh();
      };
    }, [pollSec, enabled]);

    return enabled ? state : null;
  }

  /**
   * Derive what the dashboard may claim about its data at ``nowMs``.
   *
   * Two independent staleness rules; either one marks the data stale:
   *
   *  1. No successful poll for ``max(4 * pollSec, 30s)``: the browser has
   *     lost the daemon, so nothing it shows can be trusted any more.
   *  2. The daemon's own data is older than ``2 * interval + 30s``, judged
   *     both from its ``last_refresh`` timestamp and from how long that
   *     timestamp has gone unchanged on the browser clock (the second needs
   *     no clock agreement between daemon and browser). This catches a
   *     daemon that still answers but whose check loop has stalled.
   *
   * Returns ``{ state, stale, reason, ... }`` where ``state`` is one of
   * "connecting", "unreachable" (no data yet and the last poll failed),
   * "live", "reconnecting" (last poll failed, data not yet stale) or
   * "stale" (``reason`` "disconnected" or "daemon-idle").
   */
  function freshness(live, nowMs) {
    const pollSec = (live && live.pollSec) || 8;
    const intervalSec = (live && live.refreshSec) || 30;
    const contactStaleMs = Math.max(4 * pollSec, 30) * 1000;
    const dataStaleMs = (2 * intervalSec + 30) * 1000;
    const out = {
      state: "connecting",
      stale: false,
      reason: null,
      error: live ? live.error : null,
      pollSec,
      dataAgeMs: null,
      sinceContactMs: null,
    };
    if (!live) return out;
    const lostConnection = live.connection === "error";
    if (!live.instances) {
      out.state = lostConnection ? "unreachable" : "connecting";
      return out;
    }
    out.sinceContactMs = live.lastSuccessAt !== null ? Math.max(0, nowMs - live.lastSuccessAt) : null;
    out.dataAgeMs =
      live.serverLastRefreshAt !== null ? Math.max(0, nowMs - live.serverLastRefreshAt) : null;
    const unchangedMs = live.dataSeenAt !== null ? nowMs - live.dataSeenAt : 0;
    const contactStale = out.sinceContactMs !== null && out.sinceContactMs > contactStaleMs;
    const dataStale =
      (out.dataAgeMs !== null && out.dataAgeMs > dataStaleMs) || unchangedMs > dataStaleMs;
    if (lostConnection && contactStale) {
      out.state = "stale";
      out.stale = true;
      out.reason = "disconnected";
    } else if (dataStale) {
      out.state = "stale";
      out.stale = true;
      out.reason = lostConnection ? "disconnected" : "daemon-idle";
    } else if (lostConnection) {
      out.state = "reconnecting";
    } else {
      out.state = "live";
    }
    return out;
  }

  window.CK_useLiveState = useLiveState;
  window.CK_freshness = freshness;
})();
