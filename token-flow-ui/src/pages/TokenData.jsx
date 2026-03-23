import React, { useState, useEffect, useRef, useCallback } from 'react'
import { getTokenExportUrl, postDistillAndClear, getCurrentUser } from '../api.js'

// In production VITE_API_URL is empty — derive WS URL from current window origin
// so it routes through Envoy on the same host. For local dev set VITE_API_URL=http://localhost:8001.
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
const TD     = { padding: '10px 14px', fontSize: 13, color: '#ccc',
                 borderBottom: '1px solid #1e1e30' }
const TD_R   = { ...TD, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

const MODEL_COLORS = {
  'claude-haiku':  '#a78bfa',
  'claude-sonnet': '#60a5fa',
  'claude-opus':   '#f472b6',
  'gpt-4o':        '#34d399',
  'gpt-4':         '#86efac',
  'gpt-3.5':       '#6ee7b7',
}
function modelColor(m) {
  if (!m) return '#555'
  for (const [k, v] of Object.entries(MODEL_COLORS))
    if (m.toLowerCase().includes(k)) return v
  return '#94a3b8'
}

function fmt(n)     { return n == null ? '—' : Number(n).toLocaleString() }
function fmtCost(n) { return !n ? '—' : `$${Number(n).toFixed(4)}` }
function fmtDate(s) {
  if (!s) return '—'
  return new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString()
}

function Pill({ label, color }) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 10px', borderRadius: 10,
      fontSize: 11, fontWeight: 600,
      background: `${color || ACCENT}22`, color: color || ACCENT,
      border: `1px solid ${color || ACCENT}44`,
    }}>
      {label}
    </span>
  )
}

function StatCard({ label, value, color, sub }) {
  return (
    <div style={{ ...CARD, flex: '1 1 150px' }}>
      <div style={{ fontSize: 11, color: '#666', textTransform: 'uppercase',
                    letterSpacing: 0.5, marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color: color || '#e0e0e0' }}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
      {sub && <div style={{ fontSize: 12, color: '#555', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

function LiveDot({ connected }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                   fontSize: 12, color: connected ? '#22c55e' : '#ef4444' }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: connected ? '#22c55e' : '#ef4444',
        boxShadow: connected ? '0 0 6px #22c55e88' : 'none',
        display: 'inline-block',
      }} />
      {connected ? 'live' : 'reconnecting…'}
    </span>
  )
}

export default function TokenData() {
  const [snap, setSnap]               = useState(null)
  const [connected, setConnected]     = useState(false)
  const [error, setError]             = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [opFilter, setOpFilter]       = useState('')
  const [tab, setTab]                 = useState('tokens') // 'tokens' | 'chunks'
  const [page, setPage]               = useState(0)
  const [distillStatus, setDistillStatus] = useState(null) // null | 'pending' | 'queued' | 'error'
  const [distillMsg, setDistillMsg]       = useState('')
  const wsRef     = useRef(null)
  const retryRef  = useRef(null)
  const PAGE = 50

  const connect = useCallback(() => {
    // Clean up any existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
    }
    if (retryRef.current) clearTimeout(retryRef.current)

    const ws = new WebSocket(_getWsUrl())
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      setError(null)
    }

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.error) { setError(data.error); return }
        if (data.keepalive) return  // server keepalive ping — ignore, don't wipe state
        setSnap(data)
        setLastUpdated(new Date(data.ts))
        setError(null)
      } catch {
        setError('Failed to parse message')
      }
    }

    ws.onerror = () => {
      setConnected(false)
    }

    ws.onclose = () => {
      setConnected(false)
      // Auto-reconnect after 5 s
      retryRef.current = setTimeout(connect, 5000)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
      }
    }
  }, [connect])

  // Manual refresh — send a ping; server replies with a fresh snapshot
  const refresh = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send('ping')
    }
  }

  function handleExport() {
    const url = getTokenExportUrl()
    const a = document.createElement('a')
    a.href = url
    a.download = `token-usage-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
  }

  async function handleDistill() {
    if (!window.confirm(
      'Trigger memory distillation and clear all token usage records?\n\n' +
      'The local token-flow service will run a full distill cycle and wipe the token log.'
    )) return
    setDistillStatus('pending')
    setDistillMsg('')
    try {
      const data = await postDistillAndClear()
      setDistillStatus('queued')
      setDistillMsg(`✅ Queued (msg: ${data.message_id}) — local service will process shortly.`)
      // Refresh WS data after a delay
      setTimeout(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send('ping')
        setDistillStatus(null)
      }, 8000)
    } catch (e) {
      setDistillStatus('error')
      setDistillMsg(`⚠️ ${e?.response?.data?.detail || e.message || 'Failed to queue distill trigger'}`)
    }
  }

  const summary   = snap?.summary   ?? null
  const rawEvents = snap?.events    ?? []
  const rawChunks = snap?.chunks    ?? []

  const filteredEvents = opFilter
    ? rawEvents.filter(e => e.operation === opFilter)
    : rawEvents
  const pageEvents = filteredEvents.slice(page * PAGE, (page + 1) * PAGE)
  const totalPages = Math.ceil(filteredEvents.length / PAGE)
  const allOps     = [...new Set(rawEvents.map(e => e.operation))].sort()

  const tabBtn = (active) => ({
    padding: '7px 18px', borderRadius: 8, cursor: 'pointer', fontSize: 13, fontWeight: 600,
    background: active ? ACCENT : '#2a2a40',
    color: active ? '#fff' : '#888',
    border: 'none',
  })

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    marginBottom: 24, flexWrap: 'wrap', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', margin: 0 }}>🔢 Token Data</h1>
          <LiveDot connected={connected} />
          {lastUpdated && (
            <span style={{ fontSize: 11, color: '#444' }}>
              {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <button onClick={refresh}
            style={{ background: '#2a2a40', color: '#ccc', border: '1px solid #333',
                     borderRadius: 8, padding: '7px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            ↻ Refresh
          </button>
          {!connected && (
            <button onClick={connect}
              style={{ background: '#2a2a40', color: '#ccc', border: '1px solid #333',
                       borderRadius: 8, padding: '7px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
              ↺ Reconnect
            </button>
          )}
          <button onClick={handleExport}
            style={{ background: ACCENT, color: '#fff', border: 'none',
                     borderRadius: 8, padding: '7px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            ⬇ Export CSV
          </button>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            <button
              onClick={handleDistill}
              disabled={distillStatus === 'pending'}
              style={{
                background: distillStatus === 'pending' ? '#555' : '#7c3aed',
                color: '#fff', border: 'none', borderRadius: 8,
                padding: '7px 16px', cursor: distillStatus === 'pending' ? 'default' : 'pointer',
                fontSize: 13, fontWeight: 600, opacity: distillStatus === 'pending' ? 0.7 : 1,
              }}
              title="Send distill+clear trigger to local service via SQS"
            >
              {distillStatus === 'pending' ? '⏳ Queuing…' : '🧠 Distill & Clear'}
            </button>
            {distillMsg && (
              <span style={{
                fontSize: 11, maxWidth: 260, textAlign: 'right',
                color: distillStatus === 'error' ? '#ef4444' : '#22c55e',
              }}>
                {distillMsg}
              </span>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div style={{ ...CARD, borderColor: '#ef4444', color: '#ef4444', marginBottom: 24, fontSize: 14 }}>
          ⚠️ {error}
        </div>
      )}

      {!snap && !error && (
        <div style={{ color: '#555', textAlign: 'center', paddingTop: 80, fontSize: 16 }}>
          Connecting…
        </div>
      )}

      {snap && (
        <>
          {/* Grand-total stat cards */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
            <StatCard label="Total Tokens"  value={summary?.grand_total_tokens ?? 0} color={ACCENT} />
            <StatCard label="Total Calls"   value={summary?.grand_total_calls  ?? 0} color="#60a5fa" />
            <StatCard label="Est. Cost"
              value={summary?.grand_cost_usd > 0 ? `$${Number(summary.grand_cost_usd).toFixed(4)}` : '—'}
              color="#34d399" />
            <StatCard label="Cached Chunks" value={rawChunks.length} color="#fbbf24" sub="last 100" />
          </div>

          {/* Tab switcher */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            <button style={tabBtn(tab === 'tokens')} onClick={() => setTab('tokens')}>
              🔢 Token Usage
            </button>
            <button style={tabBtn(tab === 'chunks')} onClick={() => setTab('chunks')}>
              🧩 Chunks
            </button>
          </div>

          {/* ── TOKEN USAGE TAB ─────────────────────────────────────────── */}
          {tab === 'tokens' && (
            <>
              {/* Per-operation summary */}
              {summary?.rows?.length > 0 ? (
                <div style={{ ...CARD, marginBottom: 20, overflowX: 'auto' }}>
                  <div style={{ fontSize: 15, fontWeight: 600, color: '#fff', marginBottom: 14 }}>
                    📊 Usage by Operation
                    <span style={{ fontSize: 11, color: '#555', marginLeft: 10, fontWeight: 400 }}>
                      click row to filter events below
                    </span>
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={TH}>Operation</th>
                        <th style={TH}>Model</th>
                        <th style={{ ...TH, textAlign: 'right' }}>Calls</th>
                        <th style={{ ...TH, textAlign: 'right' }}>Prompt</th>
                        <th style={{ ...TH, textAlign: 'right' }}>Completion</th>
                        <th style={{ ...TH, textAlign: 'right' }}>Total</th>
                        <th style={{ ...TH, textAlign: 'right' }}>Est. Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.rows.map((r, i) => (
                        <tr key={i}
                          style={{ cursor: 'pointer',
                            background: opFilter === r.operation ? `${ACCENT}11` : 'transparent' }}
                          onClick={() => { setOpFilter(opFilter === r.operation ? '' : r.operation); setPage(0) }}>
                          <td style={TD}><Pill label={r.operation} color={ACCENT} /></td>
                          <td style={TD}>
                            {r.model
                              ? <Pill label={r.model} color={modelColor(r.model)} />
                              : <span style={{ color: '#333' }}>—</span>}
                          </td>
                          <td style={TD_R}>{fmt(r.total_calls)}</td>
                          <td style={TD_R}>{fmt(r.prompt_tokens)}</td>
                          <td style={TD_R}>{fmt(r.completion_tokens)}</td>
                          <td style={{ ...TD_R, fontWeight: 600, color: '#e0e0e0' }}>{fmt(r.total_tokens)}</td>
                          <td style={{ ...TD_R, color: '#34d399' }}>{fmtCost(r.cost_usd)}</td>
                        </tr>
                      ))}
                      <tr style={{ background: '#0f0f1a' }}>
                        <td style={{ ...TD, fontWeight: 700, color: '#fff' }} colSpan={2}>TOTAL</td>
                        <td style={{ ...TD_R, fontWeight: 700 }}>{fmt(summary.grand_total_calls)}</td>
                        <td style={TD_R} /><td style={TD_R} />
                        <td style={{ ...TD_R, fontWeight: 700, color: ACCENT }}>{fmt(summary.grand_total_tokens)}</td>
                        <td style={{ ...TD_R, fontWeight: 700, color: '#34d399' }}>{fmtCost(summary.grand_cost_usd)}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              ) : (
                <div style={{ ...CARD, marginBottom: 20, color: '#555', textAlign: 'center', padding: 40 }}>
                  No token usage yet — AI operations will appear here automatically.
                </div>
              )}

              {/* Events table */}
              <div style={{ ...CARD, overflowX: 'auto' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                              marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, color: '#fff' }}>
                    📋 Recent Events
                    {opFilter && (
                      <span style={{ fontSize: 11, color: '#888', marginLeft: 10 }}>
                        {opFilter} ({filteredEvents.length})
                        <button onClick={() => { setOpFilter(''); setPage(0) }}
                          style={{ marginLeft: 6, background: 'none', border: 'none',
                                   color: ACCENT, cursor: 'pointer', fontSize: 11 }}>✕</button>
                      </span>
                    )}
                  </div>
                  {allOps.length > 0 && (
                    <select value={opFilter}
                      onChange={e => { setOpFilter(e.target.value); setPage(0) }}
                      style={{ background: '#0f0f1a', color: '#ccc', border: '1px solid #2a2a40',
                               borderRadius: 6, padding: '6px 10px', fontSize: 13 }}>
                      <option value=''>All operations</option>
                      {allOps.map(op => <option key={op} value={op}>{op}</option>)}
                    </select>
                  )}
                </div>

                {pageEvents.length === 0 ? (
                  <div style={{ color: '#555', textAlign: 'center', padding: 40 }}>No events.</div>
                ) : (
                  <>
                    <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 700 }}>
                      <thead>
                        <tr>
                          <th style={TH}>Time</th>
                          <th style={TH}>Operation</th>
                          <th style={TH}>Model</th>
                          <th style={TH}>Source</th>
                          <th style={{ ...TH, textAlign: 'right' }}>Prompt</th>
                          <th style={{ ...TH, textAlign: 'right' }}>Completion</th>
                          <th style={{ ...TH, textAlign: 'right' }}>Total</th>
                          <th style={{ ...TH, textAlign: 'right' }}>Cost</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pageEvents.map(ev => (
                          <tr key={ev.id}>
                            <td style={{ ...TD, fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>{fmtDate(ev.created_at)}</td>
                            <td style={TD}><Pill label={ev.operation} color={ACCENT} /></td>
                            <td style={TD}>
                              {ev.model
                                ? <Pill label={ev.model} color={modelColor(ev.model)} />
                                : <span style={{ color: '#333' }}>—</span>}
                            </td>
                            <td style={{ ...TD, fontSize: 11, color: '#555', maxWidth: 140,
                                         overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {ev.source_label || ev.user_email || '—'}
                            </td>
                            <td style={TD_R}>{fmt(ev.prompt_tokens)}</td>
                            <td style={TD_R}>{fmt(ev.completion_tokens)}</td>
                            <td style={{ ...TD_R, fontWeight: 600 }}>{fmt(ev.total_tokens)}</td>
                            <td style={{ ...TD_R, color: '#34d399' }}>{fmtCost(ev.cost_usd)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {totalPages > 1 && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                                    paddingTop: 14, justifyContent: 'flex-end' }}>
                        <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
                          style={{ background: '#2a2a40', color: page === 0 ? '#444' : '#ccc',
                                   border: '1px solid #333', borderRadius: 6,
                                   padding: '5px 12px', cursor: page === 0 ? 'default' : 'pointer', fontSize: 13 }}>
                          ← Prev
                        </button>
                        <span style={{ fontSize: 12, color: '#555' }}>{page + 1} / {totalPages}</span>
                        <button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}
                          style={{ background: '#2a2a40', color: page >= totalPages - 1 ? '#444' : '#ccc',
                                   border: '1px solid #333', borderRadius: 6,
                                   padding: '5px 12px', cursor: page >= totalPages - 1 ? 'default' : 'pointer', fontSize: 13 }}>
                          Next →
                        </button>
                      </div>
                    )}
                  </>
                )}
              </div>
            </>
          )}

          {/* ── CHUNKS TAB ──────────────────────────────────────────────── */}
          {tab === 'chunks' && (
            <div style={{ ...CARD, overflowX: 'auto' }}>
              <div style={{ fontSize: 15, fontWeight: 600, color: '#fff', marginBottom: 14 }}>
                🧩 Recent Chunks
                <span style={{ fontSize: 11, color: '#555', marginLeft: 10, fontWeight: 400 }}>
                  last 100 · ordered by created_at desc
                </span>
              </div>
              {rawChunks.length === 0 ? (
                <div style={{ color: '#555', textAlign: 'center', padding: 40 }}>No chunks yet.</div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 600 }}>
                  <thead>
                    <tr>
                      <th style={TH}>Source</th>
                      <th style={{ ...TH, textAlign: 'right' }}>#</th>
                      <th style={{ ...TH, textAlign: 'right' }}>Tokens</th>
                      <th style={{ ...TH, textAlign: 'right' }}>Score</th>
                      <th style={{ ...TH, textAlign: 'right' }}>Fact</th>
                      <th style={{ ...TH, textAlign: 'right' }}>Pref</th>
                      <th style={{ ...TH, textAlign: 'right' }}>Intent</th>
                      <th style={TH}>Summarized</th>
                      <th style={TH}>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rawChunks.map(ch => {
                      const scoreColor = ch.composite_score >= 0.7
                        ? '#22c55e' : ch.composite_score >= 0.4 ? '#fbbf24' : '#ef4444'
                      return (
                        <tr key={ch.id}>
                          <td style={{ ...TD, fontSize: 11, color: '#777', maxWidth: 160,
                                       overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {ch.source_label || '—'}
                          </td>
                          <td style={{ ...TD_R, color: '#555' }}>{ch.chunk_index}</td>
                          <td style={TD_R}>{fmt(ch.token_count)}</td>
                          <td style={{ ...TD_R, fontWeight: 700, color: scoreColor }}>
                            {ch.composite_score != null ? ch.composite_score.toFixed(2) : '—'}
                          </td>
                          <td style={{ ...TD_R, color: '#94a3b8' }}>
                            {ch.fact_score != null ? ch.fact_score.toFixed(2) : '—'}
                          </td>
                          <td style={{ ...TD_R, color: '#94a3b8' }}>
                            {ch.preference_score != null ? ch.preference_score.toFixed(2) : '—'}
                          </td>
                          <td style={{ ...TD_R, color: '#94a3b8' }}>
                            {ch.intent_score != null ? ch.intent_score.toFixed(2) : '—'}
                          </td>
                          <td style={{ ...TD, textAlign: 'center' }}>
                            {ch.is_summarized
                              ? <span style={{ color: '#22c55e' }}>✓</span>
                              : <span style={{ color: '#333' }}>—</span>}
                          </td>
                          <td style={{ ...TD, fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>
                            {fmtDate(ch.created_at)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
