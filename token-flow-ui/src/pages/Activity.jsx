import React, { useState, useEffect, useRef, useCallback } from 'react'

const _API_BASE = import.meta.env.VITE_API_URL || ''
const WS_URL = _API_BASE
  ? _API_BASE.replace(/^http/, 'ws') + '/token-data/ws'
  : (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.host + '/token-data/ws'

const ACCENT = '#e94560'
const CARD   = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }

const EVENT_META = {
  chunk:   { icon: '🧩', color: '#fbbf24', label: 'Chunk' },
  distill: { icon: '🔥', color: '#e94560', label: 'Distill / Clear' },
  clear:   { icon: '🗑',  color: '#f472b6', label: 'Clear' },
  rebuild: { icon: '🔄', color: '#34d399', label: 'Rebuild' },
  ingest:  { icon: '📥', color: '#60a5fa', label: 'Ingest' },
}
function meta(type) { return EVENT_META[type] || { icon: '⚙️', color: '#888', label: type } }

function fmtDate(s) {
  if (!s) return '—'
  return new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString()
}

function DetailPills({ detail }) {
  if (!detail || typeof detail !== 'object') return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
      {Object.entries(detail).map(([k, v]) => (
        <span key={k} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 8,
                                background: '#1e1e38', color: '#94a3b8',
                                border: '1px solid #2a2a40' }}>
          <span style={{ color: '#555' }}>{k}:</span>{' '}
          <span style={{ color: '#e0e0e0', fontWeight: 600 }}>
            {typeof v === 'string' && v.length > 40 ? '…' + v.slice(-30) : String(v)}
          </span>
        </span>
      ))}
    </div>
  )
}

export default function Activity() {
  const [events, setEvents]           = useState([])
  const [connected, setConnected]     = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [typeFilter, setTypeFilter]   = useState('')
  const wsRef    = useRef(null)
  const retryRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    if (retryRef.current) clearTimeout(retryRef.current)
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws
    ws.onopen  = () => setConnected(true)
    ws.onmessage = (e) => {
      try {
        const snap = JSON.parse(e.data)
        if (snap.pipeline_events) {
          setEvents(snap.pipeline_events)
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

  const allTypes  = [...new Set(events.map(e => e.event_type))].sort()
  const filtered  = typeFilter ? events.filter(e => e.event_type === typeFilter) : events

  // Count by type for badges
  const counts = events.reduce((acc, e) => {
    acc[e.event_type] = (acc[e.event_type] || 0) + 1
    return acc
  }, {})

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24, flexWrap: 'wrap', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', margin: 0 }}>⚡ Pipeline Activity</h1>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: connected ? '#22c55e' : '#ef4444' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: connected ? '#22c55e' : '#ef4444', boxShadow: connected ? '0 0 6px #22c55e88' : 'none', display: 'inline-block' }} />
            {connected ? 'live' : 'reconnecting…'}
          </span>
          {lastUpdated && <span style={{ fontSize: 11, color: '#444' }}>{lastUpdated.toLocaleTimeString()}</span>}
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {allTypes.length > 0 && (
            <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
              style={{ background: '#0f0f1a', color: '#ccc', border: '1px solid #2a2a40', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}>
              <option value=''>All events</option>
              {allTypes.map(t => <option key={t} value={t}>{meta(t).label}</option>)}
            </select>
          )}
          <button onClick={refresh}
            style={{ background: '#2a2a40', color: '#ccc', border: '1px solid #333', borderRadius: 8, padding: '7px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Type counters */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 24 }}>
        {Object.entries(counts).sort().map(([type, count]) => {
          const m = meta(type)
          return (
            <div key={type} onClick={() => setTypeFilter(typeFilter === type ? '' : type)}
              style={{ ...CARD, flex: '0 0 auto', padding: '12px 20px', cursor: 'pointer',
                       borderColor: typeFilter === type ? m.color : '#2a2a40' }}>
              <div style={{ fontSize: 18, marginBottom: 4 }}>{m.icon}</div>
              <div style={{ fontSize: 11, color: m.color, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>{m.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#e0e0e0' }}>{count}</div>
            </div>
          )
        })}
        {events.length === 0 && (
          <div style={{ color: '#444', fontSize: 14 }}>
            No pipeline activity yet — token events will appear here as the local service runs.
          </div>
        )}
      </div>

      {/* Event feed */}
      {filtered.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(ev => {
            const m = meta(ev.event_type)
            return (
              <div key={ev.id} style={{ ...CARD, padding: '16px 20px',
                                        borderLeft: `3px solid ${m.color}` }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                              flexWrap: 'wrap', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontSize: 18 }}>{m.icon}</span>
                    <span style={{ fontSize: 14, fontWeight: 700, color: m.color }}>{m.label}</span>
                  </div>
                  <span style={{ fontSize: 11, color: '#444' }}>{fmtDate(ev.created_at)}</span>
                </div>
                <DetailPills detail={ev.detail} />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
