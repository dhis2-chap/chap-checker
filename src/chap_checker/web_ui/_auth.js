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
 *
 * Theme handling: the modal applies its own colours INLINE (no writes to
 * :root CSS variables). Touching :root would race with `app.jsx`'s
 * `applyTheme()` and could leave keys like `--header-bg` unset after
 * sign-in. By rendering with literal colours pulled from `MODAL_THEMES`,
 * the modal stays self-contained and the artifact owns `:root` end-to-end.
 */

(function () {
  "use strict";

  function ensureAuthRoot() {
    let el = document.getElementById("ck-auth-root");
    if (!el) {
      el = document.createElement("div");
      el.id = "ck-auth-root";
      document.body.appendChild(el);
    }
    return el;
  }

  // Self-contained palette for the modal. Only the colour slots the modal
  // actually uses are here. Hex values mirror the matching keys in
  // src/app.jsx::THEMES; keep them in sync if colours change there.
  const MODAL_THEMES = {
    phosphor: {
      bg: "#050805",
      elev: "#0a0f0a",
      ink: "#6ee06e",
      inkDim: "#6a7a6a",
      inkVdim: "#3a4a3a",
      accent: "#4fbf4f",
      accentDim: "#2f5f2f",
      error: "#ff5a5a",
    },
    amber: {
      bg: "#0a0705",
      elev: "#100b07",
      ink: "#ffb84d",
      inkDim: "#8a7a5a",
      inkVdim: "#4a3a2a",
      accent: "#d9933a",
      accentDim: "#7a5a1f",
      error: "#ff5a5a",
    },
    high: {
      bg: "#000000",
      elev: "#0c0c0c",
      ink: "#9eff9e",
      inkDim: "#a0a0a0",
      inkVdim: "#606060",
      accent: "#7fff7f",
      accentDim: "#4a8a4a",
      error: "#ff5a5a",
    },
    tokyo: {
      bg: "#11131a",
      elev: "#161922",
      ink: "#7aa2f7",
      inkDim: "#7a85a8",
      inkVdim: "#3a4567",
      accent: "#5d87ee",
      accentDim: "#384a78",
      error: "#f7768e",
    },
    dhis2: {
      bg: "#c5cad0",
      elev: "#e3e7eb",
      ink: "#1e293b",
      inkDim: "#4e5b66",
      inkVdim: "#8a929c",
      accent: "#1f4a22",
      accentDim: "#b8d5ba",
      error: "#a8302f",
    },
  };

  function paletteFor(name) {
    return MODAL_THEMES[name] || MODAL_THEMES.phosphor;
  }

  function LoginModal() {
    const [visible, setVisible] = React.useState(false);
    const [themeName, setThemeName] = React.useState("phosphor");
    const [token, setToken] = React.useState("");
    const [error, setError] = React.useState("");
    const inputRef = React.useRef(null);

    // Probe /api/auth eagerly on mount. If the daemon requires a token
    // AND we don't have one stored, show the modal immediately - before
    // the artifact's first paint with INSTANCES_BASE mock data flashes
    // through. The same probe returns the configured `[ui].theme` so the
    // modal renders with the operator's colours instead of phosphor.
    // The polling hook drives subsequent re-prompts via the "needs-token"
    // event when a stored token is rejected.
    React.useEffect(() => {
      let cancelled = false;
      (async () => {
        try {
          const r = await fetch("/api/auth", { cache: "no-store" });
          if (!r.ok || cancelled) return;
          const data = await r.json();
          if (data && data.ui_theme) {
            setThemeName(data.ui_theme);
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
      (window.CK_AUTH._emit || function () {})("token-set");
    }

    const p = paletteFor(themeName);

    return (
      <div
        style={{
          // Fully opaque overlay so the artifact's INSTANCES_BASE mock
          // (rendered behind the modal while live data is still 401-blocked)
          // doesn't leak through. Inline colour - we don't touch :root so
          // the artifact owns its theme variables end-to-end.
          position: "fixed",
          inset: 0,
          background: p.bg,
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
            background: p.elev,
            border: `1px solid ${p.accentDim}`,
            color: p.ink,
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
          <div style={{ color: p.inkDim, marginBottom: "20px" }}>
            This deployment requires a bearer token to view the dashboard.
          </div>
          <label
            htmlFor="ck-token"
            style={{
              display: "block",
              color: p.inkDim,
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
              background: p.bg,
              border: `1px solid ${p.accentDim}`,
              color: p.ink,
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
                color: p.error,
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
                background: p.accentDim,
                color: p.ink,
                border: `1px solid ${p.accent}`,
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
              color: p.inkVdim,
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
