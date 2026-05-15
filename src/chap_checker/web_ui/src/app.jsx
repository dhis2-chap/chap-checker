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

function Header({ instances, alertsOn, refreshSec, clock, reload, reloadState, title }) {
  const downCount = instances.filter(i => i.status === 'down').length;
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
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:14 }}>
        <ReloadButton onClick={reload} state={reloadState} />
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

function Footer({ lastRefresh, paletteOpen }) {
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
      <span>last refresh <span style={{ color: accent }}>{lastRefresh}</span></span>
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
  const [lastRefreshAt, setLastRefreshAt] = React.useState(Date.now());
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [refreshTick, setRefreshTick] = React.useState(0);
  const [reloadState, setReloadState] = React.useState({ status: 'idle', message: '' });

  // POST /api/reload and surface success / failure as a transient
  // button label. The next /api/state poll picks up the new instance
  // set automatically, so no client-side cache invalidation is needed.
  const reload = React.useCallback(async () => {
    setReloadState({ status: 'pending', message: '' });
    try {
      const r = await fetch('/api/reload', { method: 'POST' });
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

  // CK-WIRING: live data from FastAPI's /api/state. Hook is provided by
  // web-ui/_state.js (loaded before this file) and is null'd before the
  // first response arrives so the mock INSTANCES_BASE below is used for
  // the initial paint. Replace this single block on next design-zip drop.
  const live = (typeof window !== 'undefined' && typeof window.CK_useLiveState === 'function')
    ? window.CK_useLiveState(refreshSec)
    : null;
  React.useEffect(() => {
    if (live && live.lastRefreshAt) setLastRefreshAt(live.lastRefreshAt);
  }, [live && live.lastRefreshAt]);

  // apply theme/density vars when tweaks change
  React.useEffect(() => { applyTheme(t); }, [t.theme, t.density, t.statusMode, t.crt]);

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
  React.useEffect(() => {
    const id = setInterval(() => {
      setLastRefreshAt(Date.now());
      setRefreshTick(x => x+1);
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [refreshSec]);

  // derive last refresh string
  const lastRefresh = useLastRefreshString(lastRefreshAt, now);

  // build instances: apply demoDown to PLAY-41
  const instances = React.useMemo(() => {
    // CK-WIRING: prefer live data; fall back to mock INSTANCES_BASE
    // until the first /api/state response lands.
    const base = (live && live.instances) ? live.instances : INSTANCES_BASE;
    return base.map(i => {
      const downNow = t.demoDown && i.id === 'play-41';
      // CK-WIRING: prefer real per-refresh ping history coming from the
      // server. The artifact ships buildHistory() as a deterministic
      // mulberry32 generator with a fake 3% hiccup chance per sample,
      // which is fine for design previews but renders bogus red bars
      // for instances that have actually been up. Fall back to the
      // generator only when the server hasn't yet accumulated history
      // (first /api/state response).
      const history = (Array.isArray(i.history) && i.history.length > 0)
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
      const jitter = live
        ? 0
        : ((refreshTick * 17 + i.id.charCodeAt(5)) % 30) - 10;
      return { ...i, latency: i.latency + jitter, updated: humanizeSince(lastRefreshAt, now), history };
    });
  }, [live, t.demoDown, refreshTick, lastRefreshAt, now]);

  // commands
  const commands = React.useMemo(() => [
    { id:'refresh', label:'Refresh now', hint:'r', run:() => { setLastRefreshAt(Date.now()); setRefreshTick(x=>x+1); } },
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
  ], [t.demoDown, reload]);

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
      if (e.key === 'r') { setLastRefreshAt(Date.now()); setRefreshTick(x=>x+1); }
      else if (e.key === 'f') {
        if (document.fullscreenElement) document.exitFullscreen?.();
        else document.documentElement.requestFullscreen?.();
      }
      // CK-WIRING: the artifact's `d` keybind toggled demoDown; removed
      // along with the palette entry. See the commands array above.
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen, t.demoDown]);

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
      />

      {/* grid */}
      <div style={gridStyle}>
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
      </div>

      <Footer lastRefresh={lastRefresh} paletteOpen={paletteOpen} />

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

function humanizeSince(then, now) {
  const s = Math.max(0, Math.floor((now - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s/60); return `${m}m ${s%60}s ago`;
}
function useLastRefreshString(lastAt, now) {
  return React.useMemo(() => humanizeSince(lastAt, now), [lastAt, now]);
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
