import React, { useState, useEffect, useRef, useCallback } from 'react'
import StatusBadge from '../components/StatusBadge.jsx'
import TokenMeter from '../components/TokenMeter.jsx'
import { postDistillAndClear } from '../api.js'
import { useMe } from '../hooks/useMe.js'

const BASE_URL = import.meta.env.VITE_API_URL || ''
const STREAM_INTERVAL = 10 // seconds between server pushes
const _WS_BASE = BASE_URL
  ? BASE_URL.replace(/^http/, 'ws') + '/token-data/ws'
  : (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.host + '/token-data/ws'

// Attach JWT as query param — browsers can't set Authorization headers on WebSocket
const _getWsUrl = () => {
  const token = localStorage.getItem('tf_token')
  return token ? `${_WS_BASE}?token=${encodeURIComponent(token)}` : _WS_BASE
}

const ACCENT = '#e94560'
const CARD = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{ ...CARD, flex: 1, minWidth: 140 }}>
      <div style={{ fontSize: 12, color: '#888', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color: color || '#e0e0e0' }}>{typeof value === 'number' ? value.toLocaleString() : value}</div>
      {sub && <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

export default function Dashboard() {
  const { isAdmin } = useMe()
  const [data, setData] = useState(null)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [connected, setConnected] = useState(false)
  const wsRef    = useRef(null)
  const retryRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    if (retryRef.current) clearTimeout(retryRef.current)

    const ws = new WebSocket(_getWsUrl())
    wsRef.current = ws

    ws.onopen  = () => { setConnected(true); setError(null) }
    ws.onerror = () => setConnected(false)
    ws.onclose = () => {
      setConnected(false)
      retryRef.current = setTimeout(connect, 5000)
    }
    ws.onmessage = (e) => {
      try {
        const snap = JSON.parse(e.data)
        if (snap.error) { setError(snap.error); return }
        if (snap.keepalive) return
        setData(snap.tokens || null)
        if (snap.session) setSession(snap.session)
        setLastUpdated(new Date(snap.ts))
        setError(null)
      } catch {
        setError('Failed to parse message')
      }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    }
  }, [connect])

  const [distilling, setDistilling] = useState(false)
  const [distillResult, setDistillResult] = useState(null)

  const reconnect = () => { setError(null); connect() }
  const refresh   = () => { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send('ping') }

  const handleDistill = async () => {
    if (!window.confirm('Trigger distill & clear? This will summarize session memory and reset token usage.')) return
    setDistilling(true)
    setDistillResult(null)
    try {
      const res = await postDistillAndClear()
      setDistillResult({ ok: true, msg: res.message || 'Distill job queued.' })
    } catch (err) {
      setDistillResult({ ok: false, msg: err?.response?.data?.detail || err.message || 'Request failed.' })
    } finally {
      setDistilling(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff' }}>📊 Dashboard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {lastUpdated && (
            <span style={{ fontSize: 12, color: '#555' }}>
              {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: connected ? '#22c55e' : '#ef4444',
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: connected ? '#22c55e' : '#ef4444',
              boxShadow: connected ? '0 0 6px #22c55e' : 'none',
            }} />
            {connected ? 'live' : 'reconnecting…'}
          </span>
          <button onClick={refresh}
            style={{ background: '#2a2a40', color: '#ccc', border: '1px solid #333',
                     borderRadius: 8, padding: '6px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            ↻ Refresh
          </button>
          {isAdmin && (
            <button onClick={handleDistill} disabled={distilling}
              style={{ background: distilling ? '#3a1a2a' : '#4a0020', color: distilling ? '#888' : '#f87171',
                       border: '1px solid #7f1d1d', borderRadius: 8, padding: '6px 14px',
                       cursor: distilling ? 'not-allowed' : 'pointer', fontSize: 13, fontWeight: 600 }}>
              {distilling ? '⏳ Distilling…' : '🧹 Distill & Clear'}
            </button>
          )}
        </div>
      </div>

      {distillResult && (
        <div style={{ ...CARD, borderColor: distillResult.ok ? '#22c55e' : '#ef4444',
                      color: distillResult.ok ? '#22c55e' : '#ef4444', marginBottom: 16,
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{distillResult.ok ? '✅' : '❌'} {distillResult.msg}</span>
          <button onClick={() => setDistillResult(null)}
            style={{ background: 'transparent', border: 'none', color: '#555', cursor: 'pointer', fontSize: 16 }}>✕</button>
        </div>
      )}

      {error && (
        <div style={{ ...CARD, borderColor: '#ef4444', color: '#ef4444', marginBottom: 24 }}>
          ⚠️ {error}
        </div>
      )}

      {data && (
        <>
          {/* Status Card */}
          <div style={{ ...CARD, marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <StatusBadge status={data.status} />
              <span style={{ fontSize: 15, color: '#ccc' }}>{data.message}</span>
            </div>
            <TokenMeter
              total={data.total_tokens_approx || 0}
              warn={data.warn_threshold}
              distill={data.distill_threshold}
            />
          </div>

          {/* Breakdown Stats */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
            <StatCard
              label="Session Tokens"
              value={data.session_tokens || 0}
              sub={`${data.session_files || 0} files`}
              color="#60a5fa"
            />
            <StatCard
              label="Memory Tokens"
              value={data.memory_tokens || 0}
              color="#34d399"
            />
            <StatCard
              label="Total Approx"
              value={data.total_tokens_approx || 0}
              color={ACCENT}
            />
          </div>

          {/* Cache Stats */}
          <div style={{ ...CARD, marginBottom: 24 }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>🗄️ Chunk Cache</div>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <StatCard
                label="Cached Chunks"
                value={data.cached_chunks || 0}
                color="#fbbf24"
              />
              <StatCard
                label="Cached Tokens"
                value={data.cached_chunk_tokens || 0}
                color="#fb923c"
              />
            </div>
          </div>

          {/* Active Session */}
          {session && (
            <div style={{ ...CARD }}>
              <div style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>🖥️ Active Local Session</div>
              {session.session_id ? (
                <>
                  <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
                    <StatCard
                      label="Session Tokens"
                      value={session.token_count_approx || 0}
                      sub="approx (chars / 4)"
                      color="#38bdf8"
                    />
                    <StatCard
                      label="Messages"
                      value={session.message_count || 0}
                      color="#a3e635"
                    />
                    {session.channel && (
                      <StatCard
                        label="Channel"
                        value={session.channel}
                        color="#c084fc"
                      />
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: '#555', fontFamily: 'monospace', wordBreak: 'break-all' }}>
                    <div><span style={{ color: '#444' }}>ID:</span> {session.session_id}</div>
                    {session.started_at && (
                      <div style={{ marginTop: 4 }}><span style={{ color: '#444' }}>Started:</span> {new Date(session.started_at).toLocaleString()}</div>
                    )}
                    {session.last_updated_at && (
                      <div style={{ marginTop: 4 }}><span style={{ color: '#444' }}>Last activity:</span> {new Date(session.last_updated_at).toLocaleString()}</div>
                    )}
                  </div>
                </>
              ) : (
                <div style={{ color: '#555', fontSize: 14 }}>No active session found.</div>
              )}
            </div>
          )}
        </>
      )}

      {!lastUpdated && !error && (
        <div style={{ color: '#555', textAlign: 'center', paddingTop: 80, fontSize: 16 }}>Loading...</div>
      )}

      {lastUpdated && !data && (
        <div style={{ ...CARD, color: '#666', textAlign: 'center', padding: 40 }}>
          No local session data — this view is only available on the host machine.
        </div>
      )}
    </div>
  )
}
