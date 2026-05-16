/* eslint-disable no-undef */
/**
 * Login modal + sign-out hook. Lives outside the designer artifact (`src/*`)
 * so it survives the next design-zip drop. Pairs with `_state.js`'s
 * `window.CK_AUTH` bus:
 *
 *  - `_state.js` fires `needs-token` when `/api/state` returns 401.
 *  - This file listens, mounts a modal overlay into its own DOM root, and
 *    on submit calls `CK_AUTH.writeToken(value)` + emits `token-set` so
 *    `_state.js` refetches immediately.
 *
 * The modal is hand-rolled JSX (Babel-standalone compiles inline) so it
 * doesn't depend on any of the designer's component primitives.
 */

(function () {
  "use strict";

  // Mount onto a sibling div so the artifact's React root isn't disturbed.
  // index.html only ships `<div id="root">`; create our own.
  function ensureAuthRoot() {
    let el = document.getElementById("ck-auth-root");
    if (!el) {
      el = document.createElement("div");
      el.id = "ck-auth-root";
      document.body.appendChild(el);
    }
    return el;
  }

  // Theme subsets used by the login modal. Embedded here (not imported
  // from app.jsx) because:
  //   1. The artifact in src/ is designer-replaceable; depending on its
  //      THEMES export would break on the next drop.
  //   2. Only the CSS variables the modal actually touches need to be
  //      here. The artifact applies its full dict via `applyTheme()`
  //      once `/api/state` returns; this is just the pre-paint subset.
  // Keep in sync with the matching entries in src/app.jsx::THEMES if
  // colours change there.
  const MODAL_THEMES = {
    phosphor: {
      "--bg": "#050805", "--bg-elev": "#0a0f0a",
      "--green": "#6ee06e", "--green-2": "#4fbf4f", "--green-dim": "#2f5f2f",
      "--ink-dim": "#6a7a6a", "--ink-vdim": "#3a4a3a",
      "--red": "#ff5a5a",
    },
    amber: {
      "--bg": "#0a0705", "--bg-elev": "#100b07",
      "--green": "#ffb84d", "--green-2": "#d9933a", "--green-dim": "#7a5a1f",
      "--ink-dim": "#8a7a5a", "--ink-vdim": "#4a3a2a",
      "--red": "#ff5a5a",
    },
    high: {
      "--bg": "#000000", "--bg-elev": "#0c0c0c",
      "--green": "#9eff9e", "--green-2": "#7fff7f", "--green-dim": "#4a8a4a",
      "--ink-dim": "#a0a0a0", "--ink-vdim": "#606060",
      "--red": "#ff5a5a",
    },
    tokyo: {
      "--bg": "#11131a", "--bg-elev": "#161922",
      "--green": "#7aa2f7", "--green-2": "#5d87ee", "--green-dim": "#384a78",
      "--ink-dim": "#7a85a8", "--ink-vdim": "#3a4567",
      "--red": "#f7768e",
    },
    dhis2: {
      "--bg": "#c5cad0", "--bg-elev": "#e3e7eb",
      "--green": "#2e6b32", "--green-2": "#1f4a22", "--green-dim": "#b8d5ba",
      "--ink-dim": "#4e5b66", "--ink-vdim": "#8a929c",
      "--red": "#a8302f",
    },
  };

  /**
   * Apply the named theme's CSS variables to `:root`. Idempotent and
   * cheap. Called before the login modal becomes visible so it inherits
   * the configured `[ui].theme` instead of the phosphor defaults baked
   * into index.html's <style> block.
   *
   * When the artifact (`app.jsx`) later renders and calls its own
   * applyTheme(), it writes the same (or a superset of) variables, so
   * this pre-paint apply is a no-op from the artifact's perspective.
   */
  function applyModalTheme(themeName) {
    const dict = MODAL_THEMES[themeName] || MODAL_THEMES.phosphor;
    const root = document.documentElement;
    Object.entries(dict).forEach(([k, v]) => root.style.setProperty(k, v));
  }

  function LoginModal() {
    const [visible, setVisible] = React.useState(false);
    const [token, setToken] = React.useState("");
    const [error, setError] = React.useState("");
    const inputRef = React.useRef(null);

    // Probe /api/auth eagerly on mount. If the daemon requires a token
    // AND we don't have one stored, show the modal immediately - before
    // the artifact's first paint with INSTANCES_BASE mock data flashes
    // through. The polling hook will still drive subsequent re-prompts
    // via the "needs-token" event when a stored token is rejected.
    //
    // The same probe also returns the configured `[ui].theme`. Applying
    // it here means the login modal honours the operator's theme on
    // first paint instead of flashing phosphor-green until the artifact
    // gets data from /api/state (which it can't, when auth blocks).
    React.useEffect(() => {
      let cancelled = false;
      (async () => {
        try {
          const r = await fetch("/api/auth", { cache: "no-store" });
          if (!r.ok || cancelled) return;
          const data = await r.json();
          if (data && data.ui_theme) {
            applyModalTheme(data.ui_theme);
          }
          if (data && data.required && !window.CK_AUTH.readToken()) {
            setVisible(true);
          }
        } catch (_e) {
          // If the probe fails we just wait for the polling hook's 401
          // to fire "needs-token" instead. No worse than today.
        }
      })();
      return () => {
        cancelled = true;
      };
    }, []);

    React.useEffect(() => {
      const off = window.CK_AUTH.on("needs-token", () => {
        // Clear the previous-attempt token from the input but keep
        // localStorage intact - the modal re-shows on every 401, the
        // operator gets to retry.
        setToken("");
        setError(window.CK_AUTH.readToken() ? "Token was rejected. Try again." : "");
        setVisible(true);
      });
      return off;
    }, []);

    React.useEffect(() => {
      if (visible && inputRef.current) {
        inputRef.current.focus();
      }
    }, [visible]);

    if (!visible) return null;

    function submit(e) {
      if (e && e.preventDefault) e.preventDefault();
      const trimmed = (token || "").trim();
      if (!trimmed) {
        setError("Token can't be empty.");
        return;
      }
      window.CK_AUTH.writeToken(trimmed);
      setVisible(false);
      // Let _state.js refetch immediately. If the token is wrong the next
      // 401 will pop the modal back up with the "rejected" message.
      (window.CK_AUTH._emit || function () {})("token-set");
    }

    return (
      <div
        style={{
          // Fully opaque overlay. The artifact ships a designer-time
          // INSTANCES_BASE mock so the dashboard has something to paint
          // before the first live /api/state response - which means the
          // operator-facing tiles would leak through a translucent
          // backdrop until auth resolves. Solid var(--bg) hides it.
          position: "fixed",
          inset: 0,
          background: "var(--bg, #050805)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 100000,
          fontFamily:
            "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        }}
      >
        <form
          onSubmit={submit}
          style={{
            background: "var(--bg-elev, #0a0f0a)",
            border: "1px solid var(--green-dim, #2f5f2f)",
            color: "var(--green, #6ee06e)",
            padding: "28px 32px",
            minWidth: "420px",
            maxWidth: "560px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
          }}
        >
          <div
            style={{
              fontSize: "16px",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              marginBottom: "4px",
            }}
          >
            chap-checker
          </div>
          <div style={{ color: "var(--ink-dim, #6a7a6a)", marginBottom: "20px" }}>
            This deployment requires a bearer token to view the dashboard.
          </div>
          <label
            htmlFor="ck-token"
            style={{
              display: "block",
              color: "var(--ink-dim, #6a7a6a)",
              fontSize: "12px",
              marginBottom: "6px",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            token
          </label>
          <input
            id="ck-token"
            ref={inputRef}
            type="password"
            autoComplete="off"
            spellCheck="false"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            style={{
              width: "100%",
              boxSizing: "border-box",
              background: "var(--bg, #050805)",
              border: "1px solid var(--green-dim, #2f5f2f)",
              color: "var(--green, #6ee06e)",
              padding: "10px 12px",
              fontSize: "13px",
              fontFamily: "inherit",
              outline: "none",
            }}
          />
          {error ? (
            <div
              style={{
                marginTop: "10px",
                color: "var(--red, #ff5a5a)",
                fontSize: "12px",
              }}
            >
              {error}
            </div>
          ) : null}
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "20px" }}>
            <button
              type="submit"
              style={{
                background: "var(--green-dim, #2f5f2f)",
                color: "var(--green, #6ee06e)",
                border: "1px solid var(--green-2, #4fbf4f)",
                padding: "8px 18px",
                fontSize: "13px",
                fontFamily: "inherit",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                cursor: "pointer",
              }}
            >
              Sign in
            </button>
          </div>
          <div
            style={{
              marginTop: "18px",
              color: "var(--ink-vdim, #3a4a3a)",
              fontSize: "11px",
              lineHeight: 1.5,
            }}
          >
            The token is stored in your browser's localStorage. Use the command
            palette's "Sign out" entry to clear it.
          </div>
        </form>
      </div>
    );
  }

  // Patch the auth bus with a tiny `_emit` so this file (which doesn't own
  // the listeners array) can trigger refetches. `_state.js` already has an
  // internal `emit` but it's not exposed; keep this as a public escape hatch.
  if (!window.CK_AUTH._emit) {
    const _origOn = window.CK_AUTH.on;
    const cbs = { "token-set": [] };
    window.CK_AUTH.on = function (evt, fn) {
      // Pipe both into the original (so _state.js sees `token-set`) AND
      // into a local map so this file can emit synthetic events too.
      const off1 = _origOn(evt, fn);
      if (cbs[evt]) cbs[evt].push(fn);
      return () => {
        off1();
        if (cbs[evt]) cbs[evt] = cbs[evt].filter((f) => f !== fn);
      };
    };
    window.CK_AUTH._emit = function (evt) {
      (cbs[evt] || []).forEach((f) => {
        try {
          f();
        } catch (_e) {
          // ignore
        }
      });
    };
  }

  // Mount the modal. Babel-standalone transforms this `<LoginModal />` JSX
  // inline because of the `type="text/babel"` script tag in index.html.
  function start() {
    const root = ReactDOM.createRoot(ensureAuthRoot());
    root.render(<LoginModal />);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
