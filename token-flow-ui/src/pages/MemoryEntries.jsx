import React, { useState, useEffect, useRef, useCallback } from 'react'

const _API_BASE = import.meta.env.VITE_API_URL || ''
const _WS_BASE = _API_BASE
  ? _API_BASE.replace(/^http/, 'ws') + '/token-data/ws'
  : (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.host + '/token-data/ws'

// Attach JWT as query param — browsers can't set Authorization headers on WebSocket
const _getWsUrl = () => {
  const token = localStorage.getItem('tf_token')
  return token ? `${_WS_BASE}?token=${encodeURIComponent(token)}` : _WS_BASE
}

const ACCENT = '#e94560'
const CARD   = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }
const TH     = { padding: '10px 14px', textAlign: 'left', fontSize: 12, color: '#888',
                 textTransform: 'uppercase', letterSpacing: 0.5,
                 borderBottom: '1px solid #2a2a40', whiteSpace: 'nowrap' }
const TD     = { padding: '10px 14px', fontSize: 13, color: '#ccc', borderBottom: '1px solid #1e1e30' }
const TD_R   = { ...TD, textAlign: 'right' }

const CAT_COLORS = {
  infrastructure: '#60a5fa', frontend: '#a78bfa', backend: '#34d399',
  auth: '#f472b6', deployment: '#fbbf24', feature: '#e94560',
  fix: '#fb923c', config: '#94a3b8', general: '#555',
}
function catColor(c) { return CAT_COLORS[c] || '#555' }

function scoreBar(r) {
  const pct = Math.round(r * 100)
  const col = r >= 0.8 ? '#22c55e' : r >= 0.6 ? '#fbbf24' : '#ef4444'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 6, background: '#2a2a40', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color: col, width: 30, textAlign: 'right' }}>{r.toFixed(2)}</span>
    </div>
  )
}

function fmtDate(s) {
  if (!s) return '—'
  return new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString()
}

export default function MemoryEntries() {
  const [entries, setEntries]         = useState([])
  const [connected, setConnected]     = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [expanded, setExpanded]       = useState(null)
  const [catFilter, setCatFilter]     = useState('')
  const wsRef    = useRef(null)
  const retryRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    if (retryRef.current) clearTimeout(retryRef.current)
    const ws = new WebSocket(_getWsUrl())
    wsRef.current = ws
    ws.onopen  = () => setConnected(true)
    ws.onmessage = (e) => {
      try {
        const snap = JSON.parse(e.data)
        if (snap.memory_entries) {
          setEntries(snap.memory_entries)
          setLastUpdated(new Date(snap.ts))
        }
      } catch {}
    }
    ws.onerror = () => setConnected(false)
    ws.onclose = () => { setConnected(false); retryRef.current = setTimeout(connect, 5000) }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    }
  }, [connect])

  const refresh = () => { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send('ping') }

  const allCats = [...new Set(entries.map(e => e.category).filter(Boolean))].sort()
  const filtered = catFilter ? entries.filter(e => e.category === catFilter) : entries

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24, flexWrap: 'wrap', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', margin: 0 }}>🧠 Memory Entries</h1>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: connected ? '#22c55e' : '#ef4444' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: connected ? '#22c55e' : '#ef4444', boxShadow: connected ? '0 0 6px #22c55e88' : 'none', display: 'inline-block' }} />
            {connected ? 'live' : 'reconnecting…'}
          </span>
          {lastUpdated && <span style={{ fontSize: 11, color: '#444' }}>{lastUpdated.toLocaleTimeString()}</span>}
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {allCats.length > 0 && (
            <select value={catFilter} onChange={e => setCatFilter(e.target.value)}
              style={{ background: '#0f0f1a', color: '#ccc', border: '1px solid #2a2a40', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}>
              <option value=''>All categories</option>
              {allCats.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          <button onClick={refresh}
            style={{ background: '#2a2a40', color: '#ccc', border: '1px solid #333', borderRadius: 8, padding: '7px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
        {allCats.map(cat => {
          const count = entries.filter(e => e.category === cat).length
          return (
            <div key={cat} onClick={() => setCatFilter(catFilter === cat ? '' : cat)}
              style={{ ...CARD, flex: '0 0 auto', padding: '12px 20px', cursor: 'pointer',
                       borderColor: catFilter === cat ? catColor(cat) : '#2a2a40' }}>
              <div style={{ fontSize: 11, color: catColor(cat), textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>{cat}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#e0e0e0' }}>{count}</div>
            </div>
          )
        })}
        {allCats.length === 0 && (
          <div style={{ color: '#444', fontSize: 14 }}>No memory entries yet — run a full cycle or ingest to populate.</div>
        )}
      </div>

      {/* Table */}
      {filtered.length > 0 && (
        <div style={{ ...CARD, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={TH}>Category</th>
                <th style={TH}>Source</th>
                <th style={TH}>Summary</th>
                <th style={{ ...TH, minWidth: 120 }}>Relevance</th>
                <th style={TH}>Created</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(entry => (
                <React.Fragment key={entry.id}>
                  <tr onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                    style={{ cursor: 'pointer', background: expanded === entry.id ? '#1e1e38' : 'transparent' }}
                    onMouseEnter={e => { if (expanded !== entry.id) e.currentTarget.style.background = '#1a1a2e' }}
                    onMouseLeave={e => { if (expanded !== entry.id) e.currentTarget.style.background = 'transparent' }}>
                    <td style={TD}>
                      <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 8, fontSize: 11,
                                     fontWeight: 600, background: `${catColor(entry.category)}22`,
                                     color: catColor(entry.category), border: `1px solid ${catColor(entry.category)}44` }}>
                        {entry.category || '—'}
                      </span>
                    </td>
                    <td style={{ ...TD, fontSize: 11, color: '#60a5fa', maxWidth: 140,
                                 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {entry.source_file || '—'}
                    </td>
                    <td style={{ ...TD, maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {entry.summary || '—'}
                    </td>
                    <td style={{ ...TD, minWidth: 140 }}>{scoreBar(entry.relevance ?? 0)}</td>
                    <td style={{ ...TD, fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>{fmtDate(entry.created_at)}</td>
                  </tr>
                  {expanded === entry.id && (
                    <tr>
                      <td colSpan={5} style={{ padding: '16px', background: '#0f1020', borderBottom: '2px solid #2a2a40' }}>
                        <div style={{ marginBottom: 8, fontSize: 12, color: '#888' }}>
                          <strong style={{ color: '#aaa' }}>Keywords:</strong>{' '}
                          {(() => {
                            try { return (JSON.parse(entry.keywords || '[]')).filter(k => k.length !== 32).join(', ') || '—' }
                            catch { return '—' }
                          })()}
                        </div>
                        <div style={{ fontSize: 13, color: '#ccc', lineHeight: 1.6 }}>{entry.summary || '—'}</div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
