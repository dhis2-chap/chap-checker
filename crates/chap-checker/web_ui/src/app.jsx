// app.jsx — chap-checker dashboard

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "density": "default",
  "statusMode": "subtle",
  "centerViz": "bars",
  "showUrl": true,
  "showChecks": true,
  "showMetrics": true,
  "theme": "phosphor",
  "layout": "auto",
  "crt": false,
  "demoDown": false
}/*EDITMODE-END*/;

const THEMES = {
  phosphor: {
    '--bg':'#050805','--bg-elev':'#0a0f0a',
    '--green':'#6ee06e','--green-2':'#4fbf4f','--green-dim':'#2f5f2f','--green-vdim':'#1c331c',
    '--ink-dim':'#6a7a6a','--ink-vdim':'#3a4a3a',
  },
  amber: {
    '--bg':'#0a0705','--bg-elev':'#100b07',
    '--green':'#ffb84d','--green-2':'#d9933a','--green-dim':'#7a5a1f','--green-vdim':'#3a2a0e',
    '--ink-dim':'#8a7a5a','--ink-vdim':'#4a3a2a',
  },
  high: {
    '--bg':'#000000','--bg-elev':'#0c0c0c',
    '--green':'#9eff9e','--green-2':'#7fff7f','--green-dim':'#4a8a4a','--green-vdim':'#2a4a2a',
    '--ink-dim':'#a0a0a0','--ink-vdim':'#606060',
  },
  // Borrowed from musickit's default dark theme — Tokyo Night style:
  // deep navy ground with a soft cobalt accent. Rebinds the artifact's
  // `--green*` slots to blues so the rest of the CSS stays untouched.
  tokyo: {
    '--bg':'#11131a','--bg-elev':'#161922',
    '--green':'#7aa2f7','--green-2':'#5d87ee','--green-dim':'#384a78','--green-vdim':'#1c2740',
    '--ink-dim':'#7a85a8','--ink-vdim':'#3a4567',
  },
  // DHIS2-styled light mode: warm grey body, off-white tile cards,
  // calmer slate-blue header. Status colors keep their semantic
  // meaning (green/amber/red) but desaturate a notch for legibility
  // and to keep the overall palette gentle on the eyes — earlier
  // material-design shades read as harsh under prolonged viewing.
  // `--header-bg` / `--header-ink` are dhis2-only — every other
  // theme falls back to a transparent header strip through the Header
  // component's CSS fallback chain.
  dhis2: {
    // Body and "empty slot" share a single darker grey so the gaps
    // between cards, the unused grid cells, and the padding around
    // the grid all read as one continuous tray. The cards then sit
    // visibly on top via the lighter --bg-elev.
    '--bg':'#c5cad0','--bg-elev':'#e3e7eb',
    '--green':'#2e6b32','--green-2':'#1f4a22','--green-dim':'#b8d5ba','--green-vdim':'#bfc4ca',
    '--ink-dim':'#4e5b66','--ink-vdim':'#8a929c',
    '--red':'#a8302f','--red-dim':'#e8c4c4',
    '--amber':'#b85b00','--amber-dim':'#e8d2a8',
    '--header-bg':'#1f4d75','--header-ink':'#eef1f4',
    '--badge-ok-fg':'#ffffff','--badge-warn-fg':'#ffffff','--badge-down-fg':'#ffffff',
    '--palette-item-fg':'#2c353d',
    // Primary text colour, distinct from --green so titles/numbers no
    // longer read as "near-black" while OK badge / checkmarks still
    // register as green. Dark themes leave --ink unset and fall back
    // to --green, keeping the original neon look.
    '--ink':'#1e293b',
  },
};

const DENSITY = {
  default: { '--pad':'28px', '--fs-base':'13px', '--fs-title':'14px', '--fs-stat':'16px', '--fs-badge':'12px', '--fs-foot':'12px', '--fs-head':'13px', '--status-strip':'0px' },
  comfy:   { '--pad':'40px', '--fs-base':'16px', '--fs-title':'18px', '--fs-stat':'22px', '--fs-badge':'14px', '--fs-foot':'14px', '--fs-head':'16px', '--status-strip':'0px' },
  tv:      { '--pad':'48px', '--fs-base':'20px', '--fs-title':'28px', '--fs-stat':'30px', '--fs-badge':'18px', '--fs-foot':'18px', '--fs-head':'22px', '--status-strip':'0px' },
  wall:    { '--pad':'56px', '--fs-base':'24px', '--fs-title':'40px', '--fs-stat':'40px', '--fs-badge':'24px', '--fs-foot':'20px', '--fs-head':'24px', '--status-strip':'0px' },
};

// strip mode auto-adds the colored bar width
const STRIP_WIDTH = { default:'6px', comfy:'8px', tv:'12px', wall:'16px' };

// demo data
// CK-WIRING: only rendered in explicit demo mode (`?demo=1`, or the
// designer preview where _state.js is not loaded). Real users get a
// loading panel until the first /api/state response, never these.
const INSTANCES_BASE = [
  { id:'play-43', name:'PLAY-43', platform:'DHIS2', version:'2.43.0',
    url:'https://play.im.dhis2.org/stable-2-43-0', status:'ok',
    checksPassed:5, checksTotal:5, pingPassed:7, pingTotal:7, pingPct:100,
    latency:155, updated:'11s ago', uptime:100.00,
    checks:[
      {name:'ping',ok:true},{name:'system_info',ok:true},
      {name:'db',ok:true},{name:'schema',ok:true},{name:'login',ok:true},
    ],
    seed: 43, baseLat: 155 },
  { id:'play-42', name:'PLAY-42', platform:'DHIS2', version:'2.42.4.1',
    url:'https://play.im.dhis2.org/stable-2-42-4-1', status:'ok',
    checksPassed:2, checksTotal:2, pingPassed:7, pingTotal:7, pingPct:100,
    latency:150, updated:'11s ago', uptime:100.00,
    checks:[{name:'ping',ok:true},{name:'system_info',ok:true}],
    seed: 42, baseLat: 150 },
  { id:'play-41', name:'PLAY-41', platform:'DHIS2', version:'2.41.8.1',
    url:'https://play.im.dhis2.org/stable-2-41-8-1', status:'ok',
    checksPassed:2, checksTotal:2, pingPassed:7, pingTotal:7, pingPct:100,
    latency:165, updated:'11s ago', uptime:100.00,
    checks:[{name:'ping',ok:true},{name:'system_info',ok:true}],
    seed: 41, baseLat: 165 },
  { id:'play-40', name:'PLAY-40', platform:'DHIS2', version:'2.40.11',
    url:'https://play.im.dhis2.org/stable-2-40-11', status:'ok',
    checksPassed:2, checksTotal:2, pingPassed:7, pingTotal:7, pingPct:100,
    latency:158, updated:'11s ago', uptime:100.00,
    checks:[{name:'ping',ok:true},{name:'system_info',ok:true}],
    seed: 40, baseLat: 158 },
];

// deterministic pseudo-random per instance
function mulberry32(seed) {
  return function() {
    let t = seed += 0x6D2B79F5;
    t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61);
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
function buildHistory(inst, downNow) {
  const rng = mulberry32(inst.seed * 17 + 1);
  const arr = [];
  const N = 30;
  for (let i = 0; i < N; i++) {
    const jitter = (rng() - 0.5) * 30;
    const lat = Math.max(40, Math.round(inst.baseLat + jitter));
    const hiccup = rng() < 0.03;
    arr.push({ ok: !hiccup, latency: hiccup ? null : lat });
  }
  if (downNow) {
    for (let i = arr.length - 3; i < arr.length; i++) arr[i] = { ok:false, latency:null };
  }
  return arr;
}

// Theme keys that some themes set and others don't (e.g. dhis2's
// dedicated header strip + light-mode red/amber overrides). They must
// be reset on every theme swap so switching FROM dhis2 TO phosphor
// doesn't leave the blue header bar in place. Keys present in every
// theme dict (--bg, --green, ...) don't need this because setProperty
// just overwrites them.
const RESETTABLE_THEME_KEYS = [
  '--header-bg', '--header-ink',
  '--red', '--red-dim', '--amber', '--amber-dim',
  '--badge-ok-fg', '--badge-warn-fg', '--badge-down-fg',
  '--palette-item-fg',
  '--ink',
];

function applyTheme(t) {
  const root = document.documentElement;
  // theme
  RESETTABLE_THEME_KEYS.forEach(k => root.style.removeProperty(k));
  const theme = THEMES[t.theme] || THEMES.phosphor;
  Object.entries(theme).forEach(([k,v]) => root.style.setProperty(k, v));
  // density
  const dens = DENSITY[t.density] || DENSITY.default;
  Object.entries(dens).forEach(([k,v]) => root.style.setProperty(k, v));
  // strip width
  if (t.statusMode === 'strip') {
    root.style.setProperty('--status-strip', STRIP_WIDTH[t.density] || '6px');
  } else {
    root.style.setProperty('--status-strip', '0px');
  }
  // crt
  document.body.classList.toggle('crt', !!t.crt);
}

function formatClock(d) {
  const p = n => String(n).padStart(2,'0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function Header({ instances, alertsOn, refreshSec, clock, reload, reloadState, title, fresh, demoMode }) {
  const downCount = instances.filter(i => i.status === 'down').length;
  const hasData = demoMode || fresh.state === 'live' || fresh.state === 'reconnecting' || fresh.state === 'stale';
  // `--header-bg` / `--header-ink` are dhis2-theme-only; other themes
  // fall through to a transparent strip with the body's accent text
  // colour, matching the pre-existing dark-mode look.
  const headerBg = 'var(--header-bg, transparent)';
  const brandColor = 'var(--header-ink, var(--green))';
  const metaColor = 'var(--header-ink, var(--ink-dim))';
  return (
    <header style={{
      display:'flex', justifyContent:'space-between', alignItems:'center',
      padding:'10px 18px', borderBottom:'1px solid var(--green-vdim)',
      background: headerBg,
      fontSize:'var(--fs-head)', flexShrink:0,
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:14 }}>
        <span style={{ color: brandColor, fontWeight:700 }}>{title}</span>
        <Sep />
        {hasData && (
          <>
            <span style={{ color: metaColor, opacity: 0.85 }}>
              <span style={{ color: downCount > 0 ? 'var(--red)' : brandColor }}>{instances.length}</span>{' '}
              instance(s){downCount > 0 && <span style={{ color:'var(--red)' }}> · {downCount} DOWN</span>}
            </span>
            <Sep />
            <span style={{ color: metaColor, opacity: 0.85 }}>
              alerts <span style={{ color: alertsOn ? brandColor : metaColor }}>{alertsOn ? 'ON' : 'OFF'}</span>
            </span>
            <Sep />
            <span style={{ color: metaColor, opacity: 0.85 }}>refresh every {refreshSec}s</span>
            <Sep />
          </>
        )}
        {/* CK-WIRING: connection / freshness chip. Always visible so an
            operator can tell live data from a frozen snapshot at a glance. */}
        <FreshnessChip fresh={fresh} demoMode={demoMode} />
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:14 }}>
        {/* CK-WIRING: when auth is enabled (a token sits in localStorage),
            the top-right slot becomes a Sign Out button. Reload-config
            stays accessible via the Ctrl+K palette but no longer takes
            up header real-estate. With auth off, the slot is empty and
            the clock is the only thing in the top right. */}
        <SignOutButton />
        <div style={{
          color: brandColor, fontVariantNumeric:'tabular-nums',
          fontSize: 'calc(var(--fs-head) * 1.1)',
        }}>
          {clock}
        </div>
      </div>
    </header>
  );
}

function SignOutButton() {
  // CK-WIRING: only render when CK_AUTH says we're signed in. The polling
  // hook reads the token on every fetch, so localStorage is the source
  // of truth - no extra state. We do need to re-evaluate after writeToken
  // / signOut runs, so subscribe to storage events (covers other tabs)
  // and re-read every render via a state ping.
  const [hasToken, setHasToken] = React.useState(
    () => !!(typeof window !== 'undefined' && window.CK_AUTH && window.CK_AUTH.readToken())
  );
  React.useEffect(() => {
    if (typeof window === 'undefined' || !window.CK_AUTH) return;
    function refresh() {
      setHasToken(!!window.CK_AUTH.readToken());
    }
    window.addEventListener('storage', refresh);
    // Local writes don't fire `storage` (that's for cross-tab only); a
    // small interval keeps the button in sync after our own writeToken.
    const id = setInterval(refresh, 1000);
    return () => {
      window.removeEventListener('storage', refresh);
      clearInterval(id);
    };
  }, []);
  if (!hasToken) return null;
  return (
    <button
      onClick={() => window.CK_AUTH && window.CK_AUTH.signOut()}
      title="Clear the stored bearer token and reload"
      style={{
        background:'transparent',
        border:'1px solid var(--header-ink, var(--green-vdim))',
        color:'var(--header-ink, var(--ink-dim))',
        padding:'2px 10px',
        fontFamily:'inherit', fontSize:'inherit',
        cursor:'pointer',
      }}
    >
      sign out
    </button>
  );
}

function ReloadButton({ onClick, state }) {
  const isPending = state.status === 'pending';
  const label = (
    state.status === 'pending' ? 'reloading...' :
    state.status === 'ok'      ? state.message :
    state.status === 'error'   ? state.message :
                                 'reload config'
  );
  // Idle/pending colours fall through to the header-ink so the button is
  // legible on dhis2's dark blue header strip; dark themes still get the
  // original --ink-dim grey via the CSS fallback.
  const idleColor = 'var(--header-ink, var(--ink-dim))';
  const color = (
    state.status === 'error' ? 'var(--red)' :
    state.status === 'ok'    ? 'var(--header-ink, var(--green))' :
                               idleColor
  );
  return (
    <button
      onClick={onClick}
      disabled={isPending}
      title="Re-read chap-checker.toml"
      style={{
        background:'transparent',
        border:'1px solid var(--header-ink, var(--green-vdim))',
        color, padding:'2px 10px',
        fontFamily:'inherit', fontSize:'inherit',
        cursor: isPending ? 'default' : 'pointer',
        opacity: isPending ? 0.7 : 1,
      }}
    >
      {label}
    </button>
  );
}

function Sep() {
  return <span style={{ color:'var(--green-vdim)' }}>|</span>;
}

// CK-WIRING: connection / data-freshness surfaces. `fresh` comes from
// window.CK_freshness (_state.js); see its docstring for the rules.
const FRESH_TONES = {
  warn: { bg:'var(--amber)', fg:'var(--badge-warn-fg, #1a1000)', soft:'var(--amber-dim)', accent:'var(--amber)' },
  down: { bg:'var(--red)',   fg:'var(--badge-down-fg, #1a0000)', soft:'var(--red-dim)',   accent:'var(--red)' },
};

function freshnessChipSpec(fresh, demoMode) {
  if (demoMode) return { label:'DEMO DATA', tone:'warn', title:'Demonstration data (demo mode), not a live daemon' };
  switch (fresh.state) {
    case 'live':         return { label:'LIVE', tone:null, title:'Polling the daemon; data is current' };
    case 'reconnecting': return { label:'RECONNECTING', tone:'warn', title:`Last poll failed (${fresh.error || 'error'})` };
    case 'stale':        return fresh.reason === 'disconnected'
      ? { label:'DISCONNECTED', tone:'down', title:`Cannot reach the daemon (${fresh.error || 'error'})` }
      : { label:'STALE', tone:'warn', title:'The daemon has not completed a check run recently' };
    case 'unreachable':  return { label:'UNREACHABLE', tone:'down', title:`Cannot reach the daemon (${fresh.error || 'error'})` };
    default:             return { label:'CONNECTING', tone:null, title:'Waiting for the first response from the daemon' };
  }
}

function FreshnessChip({ fresh, demoMode }) {
  const spec = freshnessChipSpec(fresh, demoMode);
  const tone = spec.tone ? FRESH_TONES[spec.tone] : null;
  const ink = 'var(--header-ink, var(--green))';
  return (
    <span
      data-ck-chip={demoMode ? 'demo' : fresh.state}
      title={spec.title}
      style={{
        display:'inline-flex', alignItems:'center', gap:6,
        padding:'1px 8px', borderRadius:2, fontWeight:700, letterSpacing:'0.06em',
        fontSize:'var(--fs-badge)', lineHeight:1.4,
        background: tone ? tone.bg : 'transparent',
        color: tone ? tone.fg : ink,
        border: tone ? `1px solid ${tone.bg}` : '1px solid var(--header-ink, var(--green-dim))',
      }}
    >
      {!tone && fresh.state === 'live' && (
        <span style={{ width:7, height:7, borderRadius:'50%', background: ink, display:'inline-block' }} />
      )}
      {spec.label}
    </span>
  );
}

function FreshnessBanner({ fresh }) {
  if (fresh.state !== 'reconnecting' && fresh.state !== 'stale') return null;
  const disconnected = fresh.state === 'reconnecting' || fresh.reason === 'disconnected';
  const tone = FRESH_TONES[fresh.state === 'stale' && disconnected ? 'down' : 'warn'];
  const err = fresh.error ? ` (${fresh.error})` : '';
  const contact = fresh.sinceContactMs != null ? humanizeDuration(fresh.sinceContactMs) : null;
  const age = fresh.dataAgeMs != null ? humanizeDuration(fresh.dataAgeMs) : null;
  let title, body;
  if (fresh.state === 'reconnecting') {
    title = 'Connection to the daemon lost';
    body = `Last poll failed${err}. Showing the last data received${contact ? ` ${contact} ago` : ''}; retrying every ${fresh.pollSec}s.`;
  } else if (disconnected) {
    title = 'Disconnected - data is stale';
    body = `No answer from the daemon${contact ? ` for ${contact}` : ''}${err}. `
      + `The statuses below are from ${age ? `${age} ago` : 'the last successful poll'} and may no longer be accurate; retrying every ${fresh.pollSec}s.`;
  } else {
    title = 'Data is stale';
    body = `The daemon is reachable but has not completed a check run${age ? ` for ${age}` : ''}. The statuses below may no longer be accurate.`;
  }
  return (
    <div role="alert" data-ck-banner={fresh.state} style={{
      display:'flex', alignItems:'baseline', gap:12, flexShrink:0,
      padding:'8px 18px',
      background: tone.soft, color:'var(--ink, #fff)',
      borderBottom:`1px solid ${tone.accent}`, borderLeft:`6px solid ${tone.accent}`,
      fontSize:'var(--fs-base)',
    }}>
      <span style={{ color: tone.accent, fontWeight:700, letterSpacing:'0.04em', textTransform:'uppercase', whiteSpace:'nowrap' }}>{title}</span>
      <span>{body}</span>
    </div>
  );
}

function LoadingPanel({ fresh, connection }) {
  const unreachable = fresh.state === 'unreachable';
  const headline = unreachable
    ? 'Cannot reach the chap-checker daemon'
    : connection === 'auth'
      ? 'Waiting for sign-in'
      : 'Connecting to the chap-checker daemon';
  const detail = unreachable
    ? `${fresh.error || 'error'} · retrying every ${fresh.pollSec}s`
    : 'No instance data yet - statuses appear after the first response.';
  return (
    <div data-ck-loading={fresh.state} style={{
      flex:1, minHeight:0, display:'flex', flexDirection:'column',
      alignItems:'center', justifyContent:'center', gap:12,
      color:'var(--ink, var(--green))', textAlign:'center', padding:24,
    }}>
      <div style={{ fontSize:'var(--fs-title)', fontWeight:700, letterSpacing:'0.04em', color: unreachable ? 'var(--red)' : 'var(--ink, var(--green))' }}>
        {headline}
        {!unreachable && <span style={{ animation:'blink 1s step-end infinite', marginLeft:4 }}>_</span>}
      </div>
      <div style={{ color:'var(--ink-dim)', fontSize:'var(--fs-base)' }}>{detail}</div>
    </div>
  );
}

function Footer({ lastRefresh, lastContact, paletteOpen }) {
  // Footer mirrors the Header's `--header-bg`/`--header-ink` chain so on
  // dhis2 the chrome brackets the content with two matching blue
  // strips. Dark themes leave the vars unset and fall through to the
  // original transparent strip with `--ink-dim` text.
  const bg = 'var(--header-bg, transparent)';
  const ink = 'var(--header-ink, var(--ink-dim))';
  const accent = 'var(--header-ink, var(--green-2))';
  return (
    <footer style={{
      display:'flex', justifyContent:'space-between',
      padding:'8px 18px', borderTop:'1px solid var(--green-vdim)',
      background: bg, color: ink,
      fontSize:'var(--fs-foot)', flexShrink:0,
    }}>
      <span>
        last refresh <span style={{ color: accent }}>{lastRefresh}</span>
        {lastContact && <> · last contact <span style={{ color: accent }}>{lastContact}</span></>}
      </span>
      <span style={{ display:'flex', gap:14 }}>
        <span><Kbd>r</Kbd> refresh</span>
        <span>·</span>
        <span><Kbd>⌘K</Kbd> / <Kbd>^K</Kbd> palette</span>
      </span>
    </footer>
  );
}

function Kbd({ children }) {
  return <span style={{ color:'var(--header-ink, var(--green-2))' }}>{children}</span>;
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [now, setNow] = React.useState(new Date());
  // CK-WIRING: demo-mode-only clock for the mock cards' "updated" field.
  // Live mode derives freshness from server timestamps instead.
  const [demoRefreshAt, setDemoRefreshAt] = React.useState(Date.now());
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [refreshTick, setRefreshTick] = React.useState(0);
  const [reloadState, setReloadState] = React.useState({ status: 'idle', message: '' });

  // POST /api/reload and surface success / failure as a transient
  // button label. The next /api/state poll picks up the new instance
  // set automatically, so no client-side cache invalidation is needed.
  const reload = React.useCallback(async () => {
    setReloadState({ status: 'pending', message: '' });
    try {
      // CK-WIRING: forward the stored bearer token (if any) so reload
      // works on auth-enabled deployments. Without this, /api/reload
      // returns 401 even though the operator is signed in for state.
      const headers = (window.CK_AUTH && window.CK_AUTH.headers) ? window.CK_AUTH.headers() : {};
      const r = await fetch('/api/reload', { method: 'POST', headers });
      const body = await r.json().catch(() => ({}));
      if (r.ok) {
        const n = body.instance_count ?? '?';
        const added = (body.added || []).length;
        const removed = (body.removed || []).length;
        const delta = (added || removed)
          ? ` (+${added} -${removed})`
          : '';
        setReloadState({ status: 'ok', message: `reloaded · ${n} instance(s)${delta}` });
      } else {
        setReloadState({ status: 'error', message: `error: ${body.message || r.statusText}` });
      }
    } catch (err) {
      setReloadState({ status: 'error', message: `error: ${err}` });
    }
    setTimeout(() => setReloadState({ status: 'idle', message: '' }), 4000);
  }, []);
  const refreshSec = 8;

  // CK-WIRING: live data from FastAPI's /api/state. The hook is provided
  // by web_ui/_state.js (loaded before this file). It never yields mock
  // data: until the first response `live.instances` is null and a loading
  // panel renders. The mock INSTANCES_BASE is only used in explicit demo
  // mode - `?demo=1` in the URL, or the designer preview where _state.js
  // isn't loaded at all. Both conditions are fixed for the page's
  // lifetime, so the hook call below is stable across renders.
  const hasLiveHook = typeof window !== 'undefined' && typeof window.CK_useLiveState === 'function';
  const demoMode = !hasLiveHook
    || (typeof window !== 'undefined' && /^(1|true|yes)?$/i.test(new URLSearchParams(window.location.search).get('demo') ?? 'no'));
  const live = hasLiveHook ? window.CK_useLiveState(refreshSec, { enabled: !demoMode }) : null;
  const hasData = demoMode || !!(live && live.instances);
  const fresh = (!demoMode && typeof window.CK_freshness === 'function')
    ? window.CK_freshness(live, now.getTime())
    : { state: 'live', stale: false, pollSec: refreshSec };
  // Exposed for scripts/capture_browser.py (waits on it per scenario).
  React.useEffect(() => {
    document.documentElement.dataset.ckFreshness = demoMode ? 'demo' : fresh.state;
  }, [demoMode, fresh.state]);

  // apply theme/density vars when tweaks change. After the first
  // post-bootstrap paint, set `window.CK_THEME_BOOTSTRAPPED` so the
  // committed Playwright capture script (scripts/capture_browser.py)
  // can `wait_for_function` on it before screenshotting - otherwise
  // the page can be captured mid-reflow between the initial phosphor
  // paint and the [ui].theme-driven re-paint.
  React.useEffect(() => {
    applyTheme(t);
    if (typeof window !== 'undefined' && themeBootstrappedRef.current) {
      window.CK_THEME_BOOTSTRAPPED = true;
    }
  }, [t.theme, t.density, t.statusMode, t.crt]);

  // Bootstrap the theme from chap-checker.toml's [ui].theme on first
  // /api/state response, but only if the user hasn't already overridden
  // it via the palette (in which case t.theme will differ from the JSX
  // default). The toml is the source of truth for fresh installs;
  // localStorage palette picks win after that.
  const themeBootstrappedRef = React.useRef(false);
  React.useEffect(() => {
    if (themeBootstrappedRef.current) return;
    if (!live || !live.uiTheme) return;
    if (t.theme === TWEAK_DEFAULTS.theme && live.uiTheme !== t.theme) {
      setTweak('theme', live.uiTheme);
      // setTweak will trigger the applyTheme useEffect above, which
      // sets CK_THEME_BOOTSTRAPPED once it runs. Mark the ref now so
      // that re-run keys off it.
    } else if (typeof window !== 'undefined') {
      // No theme change needed (daemon's ui_theme matches the default,
      // or the operator already picked a theme via the palette). The
      // applyTheme effect won't re-run, so set the flag here.
      window.CK_THEME_BOOTSTRAPPED = true;
    }
    themeBootstrappedRef.current = true;
  }, [live, t.theme]);

  // Keep the browser-tab title in sync with the configured ui.title so
  // tab-switching and bookmarks reflect the operator's brand.
  React.useEffect(() => {
    if (live && live.uiTitle) document.title = live.uiTitle;
  }, [live && live.uiTitle]);

  // clock + auto refresh
  React.useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  // CK-WIRING: the artifact's fake "auto refresh" tick reset the
  // last-refresh clock every 8s whether or not anything was fetched, so a
  // dead daemon still looked fresh. It now only drives demo mode; live
  // freshness comes from server timestamps (see `fresh` above).
  React.useEffect(() => {
    if (!demoMode) return undefined;
    const id = setInterval(() => {
      setDemoRefreshAt(Date.now());
      setRefreshTick(x => x+1);
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [refreshSec, demoMode]);

  // derive last refresh / last contact strings
  const nowMs = now.getTime();
  const lastRefresh = demoMode
    ? humanizeSince(demoRefreshAt, nowMs)
    : (live && live.serverLastRefreshAt != null)
      ? humanizeSince(live.serverLastRefreshAt, nowMs)
      : hasData ? 'pending (no check run yet)' : '—';
  const lastContact = (!demoMode && live && live.connection === 'error' && live.lastSuccessAt != null)
    ? humanizeSince(live.lastSuccessAt, nowMs)
    : null;

  // build instances: apply demoDown to PLAY-41
  const instances = React.useMemo(() => {
    // CK-WIRING: live data only; the mock INSTANCES_BASE is for explicit
    // demo mode. No data yet -> empty list (the loading panel renders).
    const base = demoMode ? INSTANCES_BASE : ((live && live.instances) || []);
    return base.map(i => {
      const downNow = demoMode && t.demoDown && i.id === 'play-41';
      // CK-WIRING: live cards show the server's real per-refresh history
      // (empty slots until it accumulates). The artifact's buildHistory()
      // mulberry32 generator with its fake 3% hiccups is demo-only.
      const history = !demoMode
        ? (Array.isArray(i.history) ? i.history : [])
        : (Array.isArray(i.history) && i.history.length > 0)
          ? i.history
          : buildHistory(i, downNow);
      if (downNow) {
        return {
          ...i,
          status:'down',
          checksPassed:0, checksTotal:2,
          pingPassed:0, pingTotal:7, pingPct:0,
          uptime:99.42,
          checks:[{name:'ping',ok:false},{name:'system_info',ok:false}],
          history,
        };
      }
      // randomize latency slightly per refresh for realism (mock-data path only)
      const jitter = !demoMode
        ? 0
        : ((refreshTick * 17 + i.id.charCodeAt(5)) % 30) - 10;
      // CK-WIRING: "updated" is the daemon's per-instance check time (or
      // its global last refresh), aged against the browser clock, so it
      // keeps growing when polls fail instead of resetting.
      const updatedAt = demoMode
        ? demoRefreshAt
        : (i.updatedAt != null ? i.updatedAt : null);
      const updated = updatedAt != null ? humanizeSince(updatedAt, nowMs) : '—';
      return { ...i, latency: i.latency + jitter, updated, history };
    });
  }, [live, demoMode, t.demoDown, refreshTick, demoRefreshAt, nowMs]);

  // CK-WIRING: trigger a real server-side rerun via POST /api/refresh
  // and refetch /api/state. The artifact's `setRefreshTick` only nudges
  // local mock-jitter; without this call neither `r` nor the *Refresh
  // now* command pulled fresh data on a live deployment.
  const refreshNow = React.useCallback(async () => {
    // No local "last refresh" reset here: the timestamp only moves when
    // the daemon reports a newer last_refresh.
    setRefreshTick(x => x + 1);
    try {
      const headers = (window.CK_AUTH && window.CK_AUTH.headers) ? window.CK_AUTH.headers() : {};
      await fetch('/api/refresh', { method: 'POST', headers });
    } catch (_e) {
      // Server-side refresh failed; the next poll tick still shows
      // a real snapshot. Don't block the UI feedback.
    }
    if (window.CK_AUTH && window.CK_AUTH.emit) window.CK_AUTH.emit('refresh-now');
  }, []);

  // commands
  const commands = React.useMemo(() => [
    { id:'refresh', label:'Refresh now', hint:'r', run: refreshNow },
    { id:'reload',  label:'Reload config', hint:'re-reads chap-checker.toml', run: reload },
    { id:'fs', label:'Toggle fullscreen', hint:'f', run:() => {
        if (document.fullscreenElement) document.exitFullscreen?.();
        else document.documentElement.requestFullscreen?.();
      } },
    { id:'density-default', label:'Density: default', run:() => setTweak('density','default') },
    { id:'density-comfy',   label:'Density: comfortable', run:() => setTweak('density','comfy') },
    { id:'density-tv',      label:'Density: TV', run:() => setTweak('density','tv') },
    { id:'density-wall',    label:'Density: wall (big numbers)', run:() => setTweak('density','wall') },
    // CK-WIRING: status-subtle/tint/strip were designer-time tweaks for
    // picking how to render status (no color, card tint, or coloured
    // edge bar). The default 'subtle' is what we ship; the other two
    // are kept reachable from the (hidden) TweaksPanel for design work.
    { id:'theme-phosphor', label:'Theme: phosphor green', run:() => setTweak('theme','phosphor') },
    { id:'theme-amber',    label:'Theme: amber', run:() => setTweak('theme','amber') },
    { id:'theme-high',     label:'Theme: high contrast', run:() => setTweak('theme','high') },
    { id:'theme-tokyo',    label:'Theme: tokyo night (blue)', run:() => setTweak('theme','tokyo') },
    { id:'theme-dhis2',    label:'Theme: dhis2 (light)', run:() => setTweak('theme','dhis2') },
    // CK-WIRING: the artifact ships a demoDown toggle that swaps PLAY-41
    // into a FAIL state for design preview. With live data that's
    // misleading (real failures show real), so it's omitted from the
    // palette. The tweak + buildHistory branch stay so the rest of the
    // artifact code is untouched.
    { id:'gh',   label:'Open GitHub repository',
      run:() => window.open('https://github.com/dhis2-chap/chap-checker', '_blank', 'noopener') },
    { id:'docs', label:'Open documentation',
      run:() => window.open('https://dhis2-chap.github.io/chap-checker/', '_blank', 'noopener') },
    // CK-WIRING: bearer-token auth (0.7+). When the daemon's [auth] block
    // is set, /api/state returns 401, the survival _auth.js renders a
    // login modal, and the token gets stored in localStorage. This entry
    // lets the operator sign out from the same surface they use for
    // every other command.
    { id:'signout', label:'Sign out', hint:'clear stored token',
      run:() => { if (window.CK_AUTH) window.CK_AUTH.signOut(); } },
  ], [t.demoDown, reload, refreshNow]);

  // global keybindings
  React.useEffect(() => {
    const onKey = (e) => {
      // ignore typing in inputs / palette open
      const tag = (e.target.tagName || '').toLowerCase();
      const isTyping = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); setPaletteOpen(o => !o); return;
      }
      // CK-WIRING: Esc closes the palette from anywhere on the page, not
      // just when the palette's input has focus. The original artifact
      // only handled Esc inside the palette input; if focus moved
      // elsewhere (e.g. user clicked outside) Esc was dead.
      if (paletteOpen && e.key === 'Escape') {
        e.preventDefault(); setPaletteOpen(false); return;
      }
      if (isTyping || paletteOpen) return;
      if (e.key === 'r') { refreshNow(); }
      else if (e.key === 'f') {
        if (document.fullscreenElement) document.exitFullscreen?.();
        else document.documentElement.requestFullscreen?.();
      }
      // CK-WIRING: the artifact's `d` keybind toggled demoDown; removed
      // along with the palette entry. See the commands array above.
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen, t.demoDown, refreshNow]);

  // layout grid: auto, 2x2, 3x2, 4x1
  const isCardLayout = t.theme === 'dhis2';
  const gridGeom = React.useMemo(() => {
    const n = instances.length;
    let cols = 2, rows = 2;
    if (t.layout === '2x2') { cols=2; rows=2; }
    else if (t.layout === '3x2') { cols=3; rows=Math.ceil(n/3); }
    else if (t.layout === '4x1') { cols=4; rows=1; }
    else { // auto: based on count + density
      if (t.density === 'wall') { cols=2; rows=Math.ceil(n/2); }
      else cols = Math.min(n, n <= 4 ? 2 : 3);
      rows = Math.ceil(n/cols);
    }
    return { cols, rows };
  }, [t.layout, t.density, instances.length]);
  const gridStyle = React.useMemo(() => ({
    flex: 1, display:'grid', minHeight:0,
    gridTemplateColumns: `repeat(${gridGeom.cols}, 1fr)`,
    gridTemplateRows: `repeat(${gridGeom.rows}, 1fr)`,
    // dhis2 (light) reads as a card-based dashboard; switch hairline
    // dividers off and use a 4px gap so panels feel like seated tiles.
    // Dark themes keep the original terminal-grid hairlines.
    gap: isCardLayout ? 4 : 0,
    padding: isCardLayout ? 4 : 0,
  }), [gridGeom, isCardLayout]);

  return (
    <>
      <Header
        instances={instances}
        alertsOn={live ? !!live.alertsOn : false}
        refreshSec={live ? Math.floor(live.refreshSec || refreshSec) : refreshSec}
        clock={formatClock(now)}
        reload={reload}
        reloadState={reloadState}
        title={(live && live.uiTitle) || 'DHIS2 / Climate Instance Checker'}
        fresh={fresh}
        demoMode={demoMode}
      />

      {!demoMode && <FreshnessBanner fresh={fresh} />}

      {/* CK-WIRING: no data yet -> explicit loading / unreachable panel
          instead of demonstration cards. */}
      {!hasData && <LoadingPanel fresh={fresh} connection={live && live.connection} />}

      {/* grid. CK-WIRING: stale data is dimmed and desaturated so an old
          all-green snapshot can't pass for a healthy fleet. */}
      {hasData && <div
        data-ck-grid={fresh.stale ? 'stale' : 'fresh'}
        aria-disabled={fresh.stale || undefined}
        style={fresh.stale
          ? { ...gridStyle, opacity: 0.4, filter: 'grayscale(0.9)', transition: 'opacity 300ms, filter 300ms' }
          : { ...gridStyle, transition: 'opacity 300ms, filter 300ms' }}>
        {instances.map((inst, i) => {
          // hairline dividers between cells (terminal feel) — suppressed
          // on card-layout themes where the gap itself separates panels.
          const { cols } = gridGeom;
          const col = i % cols;
          const row = Math.floor(i / cols);
          const totalRows = Math.ceil(instances.length / cols);
          const borderRight = isCardLayout || col === cols - 1 ? 'none' : '1px solid var(--green-vdim)';
          const borderBottom = isCardLayout || row === totalRows - 1 ? 'none' : '1px solid var(--green-vdim)';
          return (
            <div
              key={inst.id}
              style={{
                borderRight, borderBottom,
                background: isCardLayout ? 'var(--bg-elev)' : 'transparent',
                minHeight:0, minWidth:0, overflow:'hidden', display:'flex',
              }}
            >
              <Card
                inst={inst}
                statusMode={t.statusMode}
                density={t.density}
                centerViz={t.centerViz}
                showUrl={t.showUrl}
                showChecks={t.showChecks}
                showMetrics={t.showMetrics}
              />
            </div>
          );
        })}
        {/* Unfilled grid cells show the body bg directly (no placeholder
            divs needed); on dhis2 that's the same darker grey as every
            gap, so empty slots blend into the surrounding tray. */}
      </div>}

      <Footer lastRefresh={lastRefresh} lastContact={lastContact} paletteOpen={paletteOpen} />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
      />

      <TweaksPanel title="Tweaks">
        <TweakSection label="Display" />
        <TweakRadio  label="Density" value={t.density}
                     options={[
                       {value:'default', label:'Default'},
                       {value:'comfy',   label:'Comfy'},
                       {value:'tv',      label:'TV'},
                       {value:'wall',    label:'Wall'},
                     ]}
                     onChange={v => setTweak('density', v)} />
        <TweakRadio  label="Status viz" value={t.statusMode}
                     options={[
                       {value:'subtle', label:'Subtle'},
                       {value:'tint',   label:'Tint'},
                       {value:'strip',  label:'Edge'},
                     ]}
                     onChange={v => setTweak('statusMode', v)} />
        <TweakSelect label="Center viz" value={t.centerViz}
                     options={[
                       {value:'none',      label:'None (empty)'},
                       {value:'bars',      label:'Uptime bars (60 checks)'},
                       {value:'sparkline', label:'Latency sparkline'},
                       {value:'glyph',     label:'Big status glyph'},
                     ]}
                     onChange={v => setTweak('centerViz', v)} />
        <TweakSelect label="Layout" value={t.layout}
                     options={[
                       {value:'auto', label:'Auto'},
                       {value:'2x2',  label:'2 × 2'},
                       {value:'3x2',  label:'3-wide'},
                       {value:'4x1',  label:'Single row'},
                     ]}
                     onChange={v => setTweak('layout', v)} />
        <TweakSelect label="Theme" value={t.theme}
                     options={[
                       {value:'phosphor', label:'Phosphor green'},
                       {value:'amber',    label:'Amber'},
                       {value:'high',     label:'High contrast'},
                       {value:'tokyo',    label:'Tokyo night'},
                       {value:'dhis2',    label:'DHIS2 (light)'},
                     ]}
                     onChange={v => setTweak('theme', v)} />

        <TweakSection label="Card content" />
        <TweakToggle label="Show URL"     value={t.showUrl}     onChange={v => setTweak('showUrl', v)} />
        <TweakToggle label="Show checks list" value={t.showChecks} onChange={v => setTweak('showChecks', v)} />
        <TweakToggle label="Show metrics" value={t.showMetrics} onChange={v => setTweak('showMetrics', v)} />

        <TweakSection label="Demo" />
        <TweakToggle label="PLAY-41 DOWN" value={t.demoDown}    onChange={v => setTweak('demoDown', v)} />
        <TweakToggle label="CRT scanlines" value={t.crt}        onChange={v => setTweak('crt', v)} />
      </TweaksPanel>
    </>
  );
}

function humanizeDuration(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}
function humanizeSince(then, now) {
  return `${humanizeDuration(now - then)} ago`;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
