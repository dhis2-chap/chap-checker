// palette.jsx — command palette modal
const { useState: usePState, useEffect: usePEffect, useMemo: usePMemo, useRef: usePRef } = React;

function CommandPalette({ open, onClose, commands }) {
  const [q, setQ] = usePState('');
  const [idx, setIdx] = usePState(0);
  const inputRef = usePRef(null);

  usePEffect(() => {
    if (open) {
      setQ(''); setIdx(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  const filtered = usePMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return commands;
    return commands.filter(c =>
      c.label.toLowerCase().includes(term) ||
      (c.hint || '').toLowerCase().includes(term)
    );
  }, [q, commands]);

  usePEffect(() => { if (idx >= filtered.length) setIdx(0); }, [filtered, idx]);

  if (!open) return null;

  const run = (cmd) => { cmd?.run?.(); onClose(); };

  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setIdx(i => Math.min(i+1, filtered.length-1)); }
    else if (e.key === 'ArrowUp')   { e.preventDefault(); setIdx(i => Math.max(i-1, 0)); }
    else if (e.key === 'Enter')     { e.preventDefault(); run(filtered[idx]); }
  };

  return (
    <div
      onClick={onClose}
      style={{
        position:'fixed', inset:0, zIndex: 1000,
        background:'rgba(0,0,0,0.55)',
        display:'flex', alignItems:'flex-start', justifyContent:'center',
        paddingTop: '14vh',
        animation:'fadeIn 120ms ease-out',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 'min(640px, 90vw)',
          background:'var(--bg-elev)',
          border:'1px solid var(--green-dim)',
          boxShadow:'0 0 0 1px rgba(0,0,0,0.5), 0 20px 60px rgba(0,0,0,0.5), 0 0 40px rgba(110,224,110,0.05)',
          display:'flex', flexDirection:'column',
        }}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={e => { setQ(e.target.value); setIdx(0); }}
          onKeyDown={onKey}
          placeholder="Type a command..."
          style={{
            background:'transparent', border:0, outline:'none',
            color:'var(--green)', padding:'14px 18px',
            fontFamily:'inherit', fontSize: 15,
            borderBottom:'1px solid var(--green-vdim)',
            caretColor:'var(--green)',
          }}
        />
        <div className="cmd-list" style={{ padding:'6px 0', maxHeight: '50vh', overflowY:'auto' }}>
          {filtered.length === 0 && (
            <div style={{ padding:'12px 18px', color:'var(--ink-dim)' }}>No matching commands</div>
          )}
          {filtered.map((c, i) => (
            <div
              key={c.id}
              onMouseEnter={() => setIdx(i)}
              onClick={() => run(c)}
              style={{
                display:'flex', justifyContent:'space-between', alignItems:'center',
                padding: '10px 18px',
                background: i === idx ? 'rgba(110,224,110,0.10)' : 'transparent',
                color: i === idx ? 'var(--green)' : '#cfd8cf',
                cursor:'default',
                fontSize: 14,
                borderLeft: i === idx ? '2px solid var(--green)' : '2px solid transparent',
                paddingLeft: i === idx ? 16 : 18,
              }}
            >
              <span>{c.label}</span>
              {c.hint && (
                <span style={{ color:'var(--ink-dim)', fontSize: 12, letterSpacing:'0.04em' }}>{c.hint}</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { CommandPalette });
