import React, { useState, useEffect, useCallback } from 'react'
import { useAuth0 } from '@auth0/auth0-react'
import { getTokens, postDistillAndClear, getCurrentSession } from '../api.js'
import StatusBadge from '../components/StatusBadge.jsx'
import TokenMeter from '../components/TokenMeter.jsx'

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
  const { user, isAuthenticated, loginWithPopup } = useAuth0()
  const [data, setData]               = useState(null)
  const [session, setSession]         = useState(null)
  const [error, setError]             = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [distilling, setDistilling]   = useState(false)
  const [distillResult, setDistillResult] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([getTokens(), getCurrentSession()])
      setData(d)
      setSession(s)
      setLastUpdated(new Date())
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 30000)
    return () => clearInterval(iv)
  }, [fetchData])

  const handleDistill = async () => {
    let identity

    if (isAuthenticated && user?.email) {
      // Use Google identity silently
      identity = user.email
      if (!window.confirm(`Trigger distill & clear as ${user.name || user.email}? This will summarize session memory and reset token usage.`)) return
    } else {
      // Not logged in — trigger Google login first
      try {
        await loginWithPopup({ authorizationParams: { connection: 'google-oauth2' } })
        return // after login, user will click again and hit the authenticated path
      } catch (e) {
        // If popup blocked or user cancelled, fall back to prompt
        identity = window.prompt('Enter your name or email to confirm distill & clear:')
        if (!identity || !identity.trim()) return
        identity = identity.trim()
        if (!window.confirm(`Trigger distill & clear as "${identity}"? This will summarize session memory and reset token usage.`)) return
      }
    }

    setDistilling(true)
    setDistillResult(null)
    try {
      const res = await postDistillAndClear(identity)
      setDistillResult({ ok: true, msg: res.message || `Distill job queued (triggered by ${identity}).` })
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
          {isAuthenticated && user && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {user.picture && <img src={user.picture} alt="" style={{ width: 22, height: 22, borderRadius: '50%' }} />}
              <span style={{ fontSize: 12, color: '#888' }}>{user.name || user.email}</span>
            </span>
          )}
          {lastUpdated && (
            <span style={{ fontSize: 12, color: '#555' }}>
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={fetchData}
            style={{
              background: ACCENT, color: '#fff', border: 'none',
              borderRadius: 8, padding: '6px 14px', cursor: 'pointer',
              fontSize: 13, fontWeight: 600,
            }}
          >
            ↻ Refresh
          </button>
          <button
            onClick={handleDistill}
            disabled={distilling}
            style={{
              background: distilling ? '#3a1a2a' : '#4a0020',
              color: distilling ? '#888' : '#f87171',
              border: '1px solid #7f1d1d', borderRadius: 8,
              padding: '6px 14px',
              cursor: distilling ? 'not-allowed' : 'pointer',
              fontSize: 13, fontWeight: 600,
            }}
          >
            {distilling ? '⏳ Distilling…' : '🧹 Distill & Clear'}
          </button>
        </div>
      </div>

      {distillResult && (
        <div style={{
          ...CARD,
          borderColor: distillResult.ok ? '#22c55e' : '#ef4444',
          color: distillResult.ok ? '#22c55e' : '#ef4444',
          marginBottom: 16,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
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

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
            <StatCard label="Session Tokens"  value={data.session_tokens || 0}  sub={`${data.session_files || 0} files`} color="#60a5fa" />
            <StatCard label="Claude Tokens"   value={data.claude_tokens || 0}   sub={`${data.claude_session_files || 0} files`} color="#a78bfa" />
            <StatCard label="Memory Tokens"   value={data.memory_tokens || 0}   color="#34d399" />
            <StatCard label="Total Approx"    value={data.total_tokens_approx || 0} color={ACCENT} />
          </div>

          <div style={{ ...CARD }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>🗄️ Chunk Cache</div>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <StatCard label="Cached Chunks" value={data.cached_chunks || 0}       color="#fbbf24" />
              <StatCard label="Cached Tokens" value={data.cached_chunk_tokens || 0} color="#fb923c" />
            </div>
          </div>

          {/* Local Service User */}
          <div style={{ ...CARD, marginTop: 16 }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>🖥️ Local Service</div>
            {session?.user_email ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                {session.user_picture
                  ? <img src={session.user_picture} alt="" style={{ width: 40, height: 40, borderRadius: '50%', border: '2px solid #2a2a40' }} />
                  : <div style={{ width: 40, height: 40, borderRadius: '50%', background: '#2a2a40', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>👤</div>
                }
                <div>
                  <div style={{ color: '#fff', fontWeight: 600, fontSize: 15 }}>{session.user_name || session.user_email}</div>
                  <div style={{ color: '#888', fontSize: 12 }}>{session.user_email}</div>
                  {session.user_last_seen && (
                    <div style={{ color: '#555', fontSize: 11, marginTop: 2 }}>
                      Last seen {new Date(session.user_last_seen).toLocaleTimeString()}
                    </div>
                  )}
                </div>
                <div style={{ marginLeft: 'auto' }}>
                  <span style={{ background: '#14532d', color: '#4ade80', fontSize: 11, padding: '2px 8px', borderRadius: 6, fontWeight: 600 }}>● Active</span>
                </div>
              </div>
            ) : (
              <div style={{ color: '#555', fontSize: 13 }}>No local service connected</div>
            )}
          </div>
        </>
      )}

      {!data && !error && (
        <div style={{ color: '#555', textAlign: 'center', paddingTop: 80, fontSize: 16 }}>Loading...</div>
      )}
    </div>
  )
}
