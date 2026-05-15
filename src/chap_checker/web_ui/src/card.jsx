// card.jsx — instance card
const { useState, useEffect, useMemo } = React;

function StatusBadge({ status, size = 'sm' }) {
  // Badge text defaults to a near-black that reads on the bright
  // status fills the dark themes use. The light-mode (dhis2) theme
  // sets `--badge-{ok,warn,down}-fg` to white because its status
  // backgrounds are dark green / dark amber / dark red, where
  // near-black text disappears into the fill.
  const map = {
    ok:   { label:'OK',   bg:'var(--green)', fg:'var(--badge-ok-fg, #001a00)' },
    warn: { label:'WARN', bg:'var(--amber)', fg:'var(--badge-warn-fg, #1a1000)' },
    down: { label:'DOWN', bg:'var(--red)',   fg:'var(--badge-down-fg, #1a0000)' },
  };
  const s = map[status] || map.ok;
  const px = size === 'lg' ? '6px 14px' : size === 'md' ? '3px 9px' : '1px 7px';
  const fs = size === 'lg' ? 'calc(var(--fs-badge) * 1.6)' : 'var(--fs-badge)';
  return (
    <span style={{
      background: s.bg, color: s.fg, padding: px, fontSize: fs,
      fontWeight: 700, letterSpacing: '0.04em', borderRadius: 2,
      display: 'inline-block', lineHeight: 1.2,
    }}>{s.label}</span>
  );
}

function Check({ name, ok, down }) {
  const color = down ? '#fff' : (ok ? 'var(--green)' : 'var(--red)');
  // `--ink` overrides primary text-only uses of --green (so they don't
  // double as status accents). Dark themes leave it unset and fall back
  // to --green, preserving the original neon look.
  const dim = down ? 'rgba(255,255,255,0.85)' : 'var(--ink, var(--green))';
  return (
    <div style={{
      display:'flex', justifyContent:'space-between', alignItems:'baseline',
      fontSize:'var(--fs-base)', color: dim,
      padding:'2px 0',
    }}>
      <span>{name}</span>
      <span style={{ color, fontSize:'1.1em', fontWeight: 700 }}>
        {ok ? '✓' : '✗ FAIL'}
      </span>
    </div>
  );
}

function Stat({ label, value, big, invert }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap: big ? 6 : 2 }}>
      <span style={{
        color: invert ? 'rgba(255,255,255,0.75)' : 'var(--ink-dim)',
        fontSize: big ? 'calc(var(--fs-base) * 1.1)' : 'var(--fs-base)',
        letterSpacing: '0.02em',
      }}>{label}</span>
      <span style={{
        color: invert ? '#fff' : 'var(--ink, var(--green))', fontWeight: 700,
        fontSize: big ? 'calc(var(--fs-stat) * 1.6)' : 'var(--fs-stat)',
        fontVariantNumeric:'tabular-nums',
        textShadow: invert ? '0 1px 0 rgba(0,0,0,0.25)' : 'none',
      }}>{value}</span>
    </div>
  );
}

function Face({ status, density }) {
  const tv = density === 'tv';
  const wall = density === 'wall';
  const colorMap = {
    ok:   'var(--green)',
    warn: 'var(--amber)',
    down: '#fff',
  };
  const size = wall
    ? 'clamp(72px, 8vw, 130px)'
    : tv
    ? 'clamp(36px, 4vw, 64px)'
    : 'clamp(22px, 2.6vw, 34px)';
  const color = colorMap[status] || colorMap.ok;
  // shared smile/meh/frown path per status
  let mouth = null;
  if (status === 'ok')    mouth = <path d="M8 14s1.5 2 4 2 4-2 4-2" />;
  else if (status === 'warn') mouth = <line x1="8.5" y1="15" x2="15.5" y2="15" />;
  else                    mouth = <path d="M16 16s-1.5-2.5-4-2.5S8 16 8 16" />;
  return (
    <svg viewBox="0 0 24 24" width={size} height={size}
         fill="none" stroke={color} strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round"
         style={{
           display:'block',
           animation: status === 'down' ? 'pulseDown 1.4s ease-in-out infinite' : 'none',
           filter: status === 'down' ? 'drop-shadow(0 0 8px rgba(255,255,255,0.35))' : 'none',
           flexShrink: 0,
         }}
         aria-label={status} role="img">
      <circle cx="12" cy="12" r="10" />
      {mouth}
      <line x1="9" y1="9.5" x2="9.01" y2="9.5" />
      <line x1="15" y1="9.5" x2="15.01" y2="9.5" />
    </svg>
  );
}

function Card({ inst, statusMode, density, centerViz, showUrl, showChecks, showMetrics }) {
  const isDown = inst.status === 'down';
  const isWarn = inst.status === 'warn';

  const tv   = density === 'tv';
  const wall = density === 'wall';

  // ─── DOWN: take over the entire card with a saturated red field ───
  if (isDown) {
    return <DownCard inst={inst} density={density} showUrl={showUrl} showChecks={showChecks} showMetrics={showMetrics} />;
  }

  // ─── healthy / warn rendering ───
  let bg = 'transparent';
  let strip = null;
  if (statusMode === 'tint') {
    if (isWarn) bg = 'rgba(255,184,77,0.08)';
    else bg = 'rgba(110,224,110,0.025)';
  }
  if (statusMode === 'strip') {
    const color = isWarn ? 'var(--amber)' : 'var(--green)';
    strip = (
      <div style={{
        position:'absolute', left:0, top:0, bottom:0,
        width: 'var(--status-strip)', background: color,
        boxShadow: `0 0 24px ${isWarn ? 'rgba(255,184,77,0.4)' : 'rgba(110,224,110,0.25)'}`,
      }} />
    );
  }

  const dot = (
    <span style={{
      display:'inline-block', width: tv?14:8, height: tv?14:8, borderRadius:'50%',
      background: isWarn ? 'var(--amber)' : 'var(--green)',
      boxShadow: isWarn ? '0 0 10px var(--amber)' : '0 0 8px rgba(110,224,110,0.6)',
      marginRight: tv?14:8, verticalAlign:'middle',
    }} />
  );

  const titleColor = isWarn ? 'var(--amber)' : 'var(--ink, var(--green))';

  return (
    <div style={{
      position:'relative',
      padding: 'var(--pad)',
      paddingLeft: statusMode === 'strip' ? `calc(var(--pad) + var(--status-strip) + 8px)` : 'var(--pad)',
      background: bg,
      display:'flex', flexDirection:'column',
      overflow:'hidden', width:'100%', minHeight:0,
    }}>
      {strip}

      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:16 }}>
        <div style={{
          color: titleColor, fontWeight: 700,
          fontSize: wall ? 'clamp(48px, 7vw, 96px)' : tv ? 'clamp(28px, 3.2vw, 48px)' : 'var(--fs-title)',
          letterSpacing: '0.02em',
        }}>
          {dot}{inst.name}
        </div>
        <div style={{ display:'flex', alignItems:'center', gap: tv ? 14 : 10 }}>
          <div style={{
            color:'var(--ink-dim)',
            fontSize: tv ? 'calc(var(--fs-title) * 1.0)' : 'var(--fs-title)',
          }}>{inst.platform} {inst.version}</div>
          <Face status={inst.status} density={density} />
        </div>
      </div>

      {showUrl && (
        <div style={{
          color:'var(--ink-vdim)', marginTop: tv ? 8 : 6,
          fontSize: tv ? 'calc(var(--fs-base) * 1.2)' : 'var(--fs-base)',
          overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
        }}>
          {inst.url}
        </div>
      )}

      <div style={{
        marginTop: tv?20:12, display:'flex', alignItems:'center',
        gap: tv?18:10, flexWrap:'wrap',
      }}>
        <StatusBadge status={inst.status} size={wall ? 'lg' : tv ? 'md' : 'sm'} />
        <span style={{ color:'var(--ink-dim)', fontSize: tv?'calc(var(--fs-base)*1.3)':'var(--fs-base)' }}>
          <span style={{ color:'var(--ink, var(--green))' }}>{inst.checksPassed}/{inst.checksTotal}</span> checks
        </span>
        <span style={{ color:'var(--ink-dim)', fontSize: tv?'calc(var(--fs-base)*1.3)':'var(--fs-base)' }}>
          <span style={{ color:'var(--ink, var(--green))' }}>{inst.pingPassed}/{inst.pingTotal}</span> ping (
          <span style={{ color: inst.pingPct === 100 ? 'var(--ink, var(--green))' : 'var(--amber)' }}>{inst.pingPct}%</span>)
        </span>
      </div>

      {showChecks && !wall && (
        <div style={{ marginTop: 14 }}>
          <div style={{
            color:'var(--ink-vdim)', fontSize:'calc(var(--fs-base) * 0.95)',
            letterSpacing:'0.08em', marginBottom: 4,
          }}>CHECKS</div>
          {inst.checks.map(c => (
            <Check key={c.name} name={c.name} ok={c.ok} />
          ))}
        </div>
      )}

      {/* spacer pushes the viz + metrics to the bottom */}
      <div style={{ flex: 1, minHeight: 0 }} />

      {/* center viz — sits just above the metrics row */}
      <CenterViz kind={centerViz} inst={inst} density={density} />

      {showMetrics && (
        <div style={{
          display:'grid', gridTemplateColumns:'repeat(3, 1fr)',
          marginTop: 16, paddingTop: tv ? 12 : 8,
        }}>
          <Stat label="latency" value={`${inst.latency}ms`} big={tv || wall} />
          <Stat label="updated" value={inst.updated} big={tv || wall} />
          <Stat label="uptime"  value={`${inst.uptime.toFixed(2)}%`} big={tv || wall} />
        </div>
      )}
    </div>
  );
}

// ─── DOWN CARD — designed to scream from across the room ───
function DownCard({ inst, density, showUrl, showChecks, showMetrics }) {
  const tv   = density === 'tv';
  const wall = density === 'wall';

  // Big block-letter DOWN — scales with density
  const downSize = wall
    ? 'clamp(96px, 14vw, 220px)'
    : tv
    ? 'clamp(56px, 7vw, 120px)'
    : 'clamp(32px, 4vw, 56px)';

  return (
    <div style={{
      position:'relative',
      padding: 'var(--pad)',
      background: 'linear-gradient(135deg, #b21111 0%, #d92020 50%, #b21111 100%)',
      color:'#fff',
      display:'flex', flexDirection:'column',
      overflow:'hidden', width:'100%', minHeight:0,
      animation: 'cardPulse 1.4s ease-in-out infinite',
      boxShadow: 'inset 0 0 0 2px #ff5a5a, inset 0 0 80px rgba(0,0,0,0.4)',
    }}>
      {/* hazard stripe overlay, very subtle */}
      <div aria-hidden style={{
        position:'absolute', inset:0, pointerEvents:'none', opacity: 0.10,
        backgroundImage: 'repeating-linear-gradient(45deg, #000 0 14px, transparent 14px 28px)',
      }} />

      {/* TOP: title + version */}
      <div style={{
        position:'relative', display:'flex',
        justifyContent:'space-between', alignItems:'center', gap:16,
      }}>
        <div style={{
          color:'#fff', fontWeight: 800,
          fontSize: wall ? 'clamp(48px, 7vw, 96px)' : tv ? 'clamp(28px, 3.2vw, 48px)' : 'var(--fs-title)',
          letterSpacing:'0.02em',
          textShadow:'0 2px 0 rgba(0,0,0,0.25)',
        }}>
          <span style={{
            display:'inline-block', width: tv?16:10, height: tv?16:10, borderRadius:'50%',
            background:'#fff', boxShadow:'0 0 20px #fff, 0 0 6px #fff',
            marginRight: tv?14:8, verticalAlign:'middle',
            animation: 'pulseDown 0.8s ease-in-out infinite',
          }} />
          {inst.name}
        </div>
        <div style={{ display:'flex', alignItems:'center', gap: tv ? 14 : 10 }}>
          <div style={{
            color:'rgba(255,255,255,0.78)',
            fontSize: tv ? 'calc(var(--fs-title) * 1.0)' : 'var(--fs-title)',
          }}>{inst.platform} {inst.version}</div>
          <Face status="down" density={density} />
        </div>
      </div>

      {showUrl && (
        <div style={{
          color:'rgba(255,255,255,0.55)', marginTop: 4,
          fontSize: tv ? 'calc(var(--fs-base) * 1.2)' : 'var(--fs-base)',
          overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
          position:'relative',
        }}>
          {inst.url}
        </div>
      )}

      {/* HERO: massive DOWN slab */}
      <div style={{
        position:'relative',
        marginTop: tv ? 18 : 10,
        display:'flex', flexDirection:'column', alignItems:'flex-start',
      }}>
        <div style={{
          fontSize: downSize, lineHeight: 0.85, fontWeight: 900,
          letterSpacing: '-0.02em', color:'#fff',
          textShadow:'0 4px 0 rgba(0,0,0,0.25), 0 0 40px rgba(255,255,255,0.3)',
        }}>
          DOWN
        </div>
        <div style={{
          marginTop: tv ? 12 : 6,
          color:'rgba(255,255,255,0.9)', fontWeight: 600,
          fontSize: tv ? 'calc(var(--fs-base) * 1.4)' : 'calc(var(--fs-base) * 1.05)',
          letterSpacing:'0.04em',
        }}>
          {inst.checksTotal - inst.checksPassed}/{inst.checksTotal} checks failing · last seen {inst.updated}
        </div>
      </div>

      {showChecks && (tv || wall) && (
        <div style={{
          position:'relative', marginTop: tv ? 16 : 12,
          paddingTop: tv ? 10 : 6,
          borderTop:'1px solid rgba(255,255,255,0.25)',
        }}>
          {inst.checks.map(c => <Check key={c.name} name={c.name} ok={c.ok} down />)}
        </div>
      )}

      {/* spacer pushes metrics to bottom */}
      <div style={{ flex: 1, minHeight: 0 }} />

      {showMetrics && (
        <div style={{
          position:'relative',
          display:'grid', gridTemplateColumns:'repeat(3, 1fr)',
          marginTop: 16, paddingTop: tv ? 12 : 8,
          borderTop:'1px solid rgba(255,255,255,0.25)',
        }}>
          <Stat label="latency" value="—" big={tv || wall} invert />
          <Stat label="updated" value={inst.updated} big={tv || wall} invert />
          <Stat label="uptime"  value={`${inst.uptime.toFixed(2)}%`} big={tv || wall} invert />
        </div>
      )}
    </div>
  );
}

// ─── keyframes ───
if (typeof document !== 'undefined' && !document.getElementById('__pulse_kf')) {
  const s = document.createElement('style');
  s.id = '__pulse_kf';
  s.textContent = `
    @keyframes pulseDown { 0%,100%{opacity:1; transform:scale(1)} 50%{opacity:.55; transform:scale(0.92)} }
    @keyframes cardPulse {
      0%, 100% { box-shadow: inset 0 0 0 2px #ff5a5a, inset 0 0 80px rgba(0,0,0,0.4), 0 0 0 0 rgba(255,90,90,0); }
      50%      { box-shadow: inset 0 0 0 2px #ff8080, inset 0 0 80px rgba(0,0,0,0.4), 0 0 40px rgba(255,90,90,0.45); }
    }
    @keyframes blink { 0%,49%{opacity:1} 50%,100%{opacity:0} }
    @keyframes fadeIn { from{opacity:0;transform:translateY(-4px)} to{opacity:1;transform:none} }
    @keyframes bannerSlide { from{transform:translateY(-100%)} to{transform:none} }
  `;
  document.head.appendChild(s);
}

// ─── CENTER VIZ — fills the empty middle of healthy cards ───
function CenterViz({ kind, inst, density }) {
  const tv = density === 'tv';
  const wall = density === 'wall';

  if (kind === 'none' || !kind) {
    return null;
  }
  if (kind === 'bars') {
    return <UptimeBars inst={inst} density={density} />;
  }
  if (kind === 'sparkline') {
    return <LatencySpark inst={inst} density={density} />;
  }
  if (kind === 'glyph') {
    return <StatusGlyph inst={inst} density={density} />;
  }
  return null;
}

function UptimeBars({ inst, density }) {
  const tv = density === 'tv';
  const wall = density === 'wall';
  const real = inst.history || [];
  const okColor   = 'var(--green)';
  const downColor = 'var(--red)';
  const warnColor = 'var(--amber)';

  // CK-WIRING: pad to a fixed 30-slot width so the bars area always
  // looks like a strip of distinct samples even on a fresh server
  // with one or two real history points. Empty slots are dim and
  // non-interactive ({noData: true}); they're not counted in the
  // uptime percentage.
  const SLOTS = 30;
  const pad = Math.max(0, SLOTS - real.length);
  const h = [
    ...Array.from({ length: pad }, () => ({ noData: true })),
    ...real,
  ];

  // CK-WIRING: the bar colour follows the per-refresh worst check
  // status from the server (s.status: 'ok' / 'warn' / 'down'). The
  // artifact's original logic flagged amber from a latency threshold,
  // which we don't care about - real failures should be the only
  // thing that paints a bar non-green.
  const successCount = real.filter(s => s.status === 'ok' || s.ok).length;
  const pct = real.length ? ((successCount / real.length) * 100) : 100;

  return (
    <div style={{
      display:'flex', flexDirection:'column',
      marginTop: tv ? 14 : 10,
      gap: tv ? 8 : 5,
    }}>
      <div style={{
        display:'flex', justifyContent:'space-between',
        color:'var(--ink-vdim)', fontSize:'calc(var(--fs-base) * 0.95)',
        letterSpacing:'0.08em',
      }}>
        <span>UPTIME · LAST 30 CHECKS</span>
        <span>{pct.toFixed(0)}% · NOW →</span>
      </div>
      <div style={{
        height: tv ? 44 : wall ? 80 : 26,
        display:'flex', alignItems:'stretch', gap: tv ? 3 : 2,
      }}>
        {h.map((s, i) => {
          // CK-WIRING: padded "no data yet" slot (see UptimeBars top).
          if (s.noData) {
            return (
              <div key={i} style={{
                flex: 1, background: 'var(--green-vdim)', opacity: 0.4,
              }} />
            );
          }
          // CK-WIRING: status drives the bar colour - green/amber/red
          // map 1:1 to the chap-checker overall worst-status for that
          // refresh. Fall back to s.ok for compatibility if a future
          // designer drop omits the status field.
          const status = s.status || (s.ok ? 'ok' : 'down');
          const color =
              status === 'warn' ? warnColor
            : status === 'ok'   ? okColor
            : downColor;
          // CK-WIRING: per-bar native tooltip. Order in `h` is oldest →
          // newest; `(h.length - i)` is "samples back from now". The
          // server keeps history points without timestamps so we use
          // relative position rather than a clock time.
          const slot = h.length - i;
          const latencyPart = s.latency != null ? ' · ' + s.latency + 'ms' : '';
          const title =
              status === 'ok'   ? `#${slot} · OK${latencyPart}`
            : status === 'warn' ? `#${slot} · WARN${latencyPart}`
            :                     `#${slot} · FAIL (no response)`;
          const isOk = status === 'ok';
          return (
            <div key={i} title={title} style={{
              flex: 1, background: color,
              opacity: isOk ? 0.9 : 1,
              boxShadow: isOk ? 'none'
                : status === 'warn' ? '0 0 8px rgba(255,184,77,0.55)'
                :                     '0 0 8px rgba(255,90,90,0.6)',
            }} />
          );
        })}
      </div>
    </div>
  );
}

function LatencySpark({ inst, density }) {
  const tv = density === 'tv';
  const h = (inst.history || []).filter(x => x.latency != null);
  if (h.length === 0) return <div style={{ flex:1 }} />;
  const latencies = h.map(x => x.latency);
  const min = Math.min(...latencies);
  const max = Math.max(...latencies);
  const range = Math.max(1, max - min);
  const w = 100, hh = 30;
  const pts = latencies.map((v, i) => {
    const x = (i / (latencies.length - 1)) * w;
    const y = hh - ((v - min) / range) * hh;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const lastY = hh - ((latencies[latencies.length-1] - min) / range) * hh;
  return (
    <div style={{
      display:'flex', flexDirection:'column',
      marginTop: tv ? 14 : 10,
      gap: tv ? 8 : 5,
    }}>
      <div style={{
        display:'flex', justifyContent:'space-between',
        color:'var(--ink-vdim)', fontSize:'calc(var(--fs-base) * 0.95)',
        letterSpacing:'0.08em',
      }}>
        <span>LATENCY · LAST 30 CHECKS</span>
        <span>{min}—{max}ms</span>
      </div>
      <svg viewBox={`0 0 ${w} ${hh}`} preserveAspectRatio="none"
           style={{ width:'100%', height: tv ? 44 : 26, display:'block' }}>
        <defs>
          <linearGradient id={`lg-${inst.id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--green)" stopOpacity="0.5" />
            <stop offset="1" stopColor="var(--green)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={`0,${hh} ${pts} ${w},${hh}`} fill={`url(#lg-${inst.id})`} />
        <polyline points={pts} fill="none" stroke="var(--green)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <circle cx={w} cy={lastY} r="1.5" fill="var(--green)" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

function StatusGlyph({ inst, density }) {
  const tv = density === 'tv';
  const wall = density === 'wall';
  const isWarn = inst.status === 'warn';
  const color = isWarn ? 'var(--amber)' : 'var(--green)';
  const glyph = isWarn ? '!' : '✓';
  return (
    <div style={{
      flex: 1, minHeight: 0,
      display:'flex', alignItems:'center', justifyContent:'center',
      flexDirection:'column', gap: 8,
    }}>
      <div style={{
        fontSize: wall ? 'clamp(120px, 16vw, 240px)' : tv ? 'clamp(80px, 10vw, 160px)' : 'clamp(48px, 7vw, 100px)',
        lineHeight: 0.9, fontWeight: 700, color,
        textShadow: `0 0 30px ${isWarn ? 'rgba(255,184,77,0.4)' : 'rgba(110,224,110,0.4)'}`,
      }}>{glyph}</div>
      <div style={{
        color:'var(--ink-dim)', letterSpacing:'0.1em',
        fontSize: tv ? 'calc(var(--fs-base) * 1.3)' : 'var(--fs-base)',
      }}>{isWarn ? 'DEGRADED' : 'HEALTHY'}</div>
    </div>
  );
}

Object.assign(window, { Card, StatusBadge, CenterViz, UptimeBars, LatencySpark, StatusGlyph });
