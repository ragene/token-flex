import React, { useState, useEffect, useCallback } from 'react'
import { getLocalSessions, distillSession, clearSessionTokens } from '../api.js'

const ACCENT = '#e94560'
const CARD   = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }

function fmt(n)     { return n == null ? '—' : Number(n).toLocaleString() }
function fmtCost(n) { return !n ? '—' : `$${Number(n).toFixed(4)}` }
function fmtDate(s) {
  if (!s) return '—'
  return new Date(s.endsWith('Z') ? s : s + 'Z').toLocaleString()
}

function Avatar({ name, picture, size = 36 }) {
  if (picture) return (
    <img src={picture} alt={name} style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover' }} />
  )
  const initials = (name || '?').split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase()
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      background: `${ACCENT}33`, border: `1px solid ${ACCENT}55`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: size * 0.38, fontWeight: 700, color: ACCENT, flexShrink: 0,
    }}>
      {initials}
    </div>
  )
}

function ActionButton({ label, onClick, danger, disabled, loading }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      style={{
        background: loading ? '#2a2a40' : danger ? '#4a0020' : '#1e1e38',
        color: loading ? '#555' : danger ? '#f87171' : '#94a3b8',
        border: `1px solid ${loading ? '#2a2a40' : danger ? '#7f1d1d' : '#2a2a40'}`,
        borderRadius: 6, padding: '5px 12px',
        cursor: disabled || loading ? 'not-allowed' : 'pointer',
        fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {loading ? '⏳' : label}
    </button>
  )
}

export default function Sessions() {
  const [sessions, setSessions]   = useState([])
  const [loading,  setLoading]    = useState(true)
  const [error,    setError]      = useState(null)
  const [actions,  setActions]    = useState({}) // { email: { distill|clear: 'pending'|'ok'|'error', msg } }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getLocalSessions()
      setSessions(data)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const setAction = (email, key, val) =>
    setActions(prev => ({ ...prev, [email]: { ...(prev[email] || {}), [key]: val } }))

  const handleDistill = async (email) => {
    if (!window.confirm(`Queue distill & clear for ${email}?`)) return
    setAction(email, 'distill', 'pending')
    try {
      const res = await distillSession(email)
      setAction(email, 'distill', { status: 'ok', msg: `Queued (${res.message_id?.slice(0,8)}…)` })
    } catch (e) {
      setAction(email, 'distill', { status: 'error', msg: e?.response?.data?.detail || e.message })
    }
  }

  const handleClear = async (email) => {
    if (!window.confirm(`Immediately delete all token_usage rows for ${email}? This cannot be undone.`)) return
    setAction(email, 'clear', 'pending')
    try {
      const res = await clearSessionTokens(email)
      setAction(email, 'clear', { status: 'ok', msg: `Cleared ${res.rows_deleted} rows` })
      load() // refresh totals
    } catch (e) {
      setAction(email, 'clear', { status: 'error', msg: e?.response?.data?.detail || e.message })
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', margin: 0 }}>🖥️ Active Sessions</h1>
        <button onClick={load}
          style={{ background: '#2a2a40', color: '#ccc', border: '1px solid #333',
                   borderRadius: 8, padding: '7px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
          ↻ Refresh
        </button>
      </div>

      {error && (
        <div style={{ ...CARD, borderColor: '#ef4444', color: '#ef4444', marginBottom: 20 }}>
          ⚠️ {error}
        </div>
      )}

      {loading && !sessions.length && (
        <div style={{ color: '#555', textAlign: 'center', paddingTop: 60 }}>Loading…</div>
      )}

      {!loading && !sessions.length && !error && (
        <div style={{ ...CARD, color: '#555', textAlign: 'center', padding: 48 }}>
          No local sessions found. Sessions appear here once users push a snapshot via the token-flow service.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {sessions.map(s => {
          const act = actions[s.email] || {}
          return (
            <div key={s.email} style={{
              ...CARD,
              borderColor: s.is_active ? `${ACCENT}66` : '#2a2a40',
              position: 'relative',
            }}>
              {s.is_active && (
                <span style={{
                  position: 'absolute', top: 12, right: 12,
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  fontSize: 11, color: '#22c55e', fontWeight: 600,
                }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#22c55e',
                                 boxShadow: '0 0 6px #22c55e', display: 'inline-block' }} />
                  active
                </span>
              )}

              <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
                <Avatar name={s.name} picture={s.picture} />
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: '#e0e0e0' }}>{s.name || s.email}</div>
                  <div style={{ fontSize: 12, color: '#555' }}>{s.email}</div>
                  {s.host && <div style={{ fontSize: 11, color: '#444', marginTop: 2 }}>🖥 {s.host}</div>}
                </div>
              </div>

              {/* Stats row */}
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 16 }}>
                <div>
                  <div style={{ fontSize: 10, color: '#555', textTransform: 'uppercase', letterSpacing: 0.5 }}>Calls</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#60a5fa' }}>{fmt(s.total_calls)}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: '#555', textTransform: 'uppercase', letterSpacing: 0.5 }}>Tokens</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: ACCENT }}>{fmt(s.total_tokens)}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: '#555', textTransform: 'uppercase', letterSpacing: 0.5 }}>Cost</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#34d399' }}>{fmtCost(s.cost_usd)}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: '#555', textTransform: 'uppercase', letterSpacing: 0.5 }}>Last seen</div>
                  <div style={{ fontSize: 13, color: '#888', marginTop: 3 }}>{fmtDate(s.last_seen)}</div>
                </div>
              </div>

              {/* Actions */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <ActionButton
                  label="🧠 Distill & Clear"
                  onClick={() => handleDistill(s.email)}
                  loading={act.distill === 'pending'}
                  disabled={act.clear === 'pending'}
                />
                <ActionButton
                  label="🗑 Clear Tokens"
                  onClick={() => handleClear(s.email)}
                  danger
                  loading={act.clear === 'pending'}
                  disabled={act.distill === 'pending'}
                />
                {act.distill && act.distill !== 'pending' && (
                  <span style={{ fontSize: 11, color: act.distill.status === 'ok' ? '#22c55e' : '#ef4444' }}>
                    {act.distill.status === 'ok' ? '✅' : '❌'} {act.distill.msg}
                  </span>
                )}
                {act.clear && act.clear !== 'pending' && (
                  <span style={{ fontSize: 11, color: act.clear.status === 'ok' ? '#22c55e' : '#ef4444' }}>
                    {act.clear.status === 'ok' ? '✅' : '❌'} {act.clear.msg}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
