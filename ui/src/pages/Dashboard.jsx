import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  getTokens, postDistillAndClear,
  startDeviceFlow, pollDeviceFlow,
  getStoredToken, storeToken, clearToken,
} from '../api.js'
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

// SSO states: null | 'checking' | 'pending_url' | 'polling' | 'done' | 'error'
export default function Dashboard() {
  const [data, setData]               = useState(null)
  const [error, setError]             = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [distilling, setDistilling]   = useState(false)
  const [distillResult, setDistillResult] = useState(null)

  // SSO device flow state
  const [ssoState, setSsoState]         = useState(null)   // null | 'checking' | 'pending_url' | 'polling' | 'done' | 'error'
  const [ssoUrl, setSsoUrl]             = useState(null)
  const [ssoCode, setSsoCode]           = useState(null)
  const [ssoError, setSsoError]         = useState(null)
  const [ssoUser, setSsoUser]           = useState(null)
  const pollTimerRef                    = useRef(null)
  const deviceCodeRef                   = useRef(null)
  const pollIntervalRef                 = useRef(5000)

  // On mount: restore SSO user from token
  useEffect(() => {
    if (getStoredToken()) setSsoState('done')
  }, [])

  const fetchData = useCallback(async () => {
    try {
      const d = await getTokens()
      setData(d)
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

  // ── SSO device flow ─────────────────────────────────────────────────────────
  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const schedulePoll = useCallback((device_code, interval) => {
    stopPolling()
    pollTimerRef.current = setTimeout(async () => {
      try {
        const res = await pollDeviceFlow(device_code)
        if (res.status === 'authorized') {
          storeToken(res.access_token)
          setSsoUser({ email: res.email, name: res.name, role: res.role })
          setSsoState('done')
          stopPolling()
          // Now fire the distill
          await fireDistill()
        } else if (res.status === 'expired') {
          setSsoState('error')
          setSsoError('Login timed out. Try again.')
        } else {
          // still pending / slow_down — keep polling
          const next = res.status === 'slow_down' ? interval + 2000 : interval
          pollIntervalRef.current = next
          schedulePoll(device_code, next)
        }
      } catch (e) {
        setSsoState('error')
        setSsoError(e?.response?.data?.detail || e.message || 'Polling failed.')
      }
    }, interval)
  }, []) // eslint-disable-line

  const startSso = useCallback(async () => {
    setSsoState('checking')
    setSsoError(null)
    setSsoUrl(null)
    setSsoCode(null)
    try {
      const res = await startDeviceFlow()
      deviceCodeRef.current    = res.device_code
      pollIntervalRef.current  = (res.interval || 5) * 1000
      setSsoUrl(res.verification_url)
      setSsoCode(res.user_code)
      setSsoState('pending_url')
      // auto-open
      window.open(res.verification_url, '_blank', 'noopener,noreferrer')
      // start polling
      schedulePoll(res.device_code, pollIntervalRef.current)
      setSsoState('polling')
    } catch (e) {
      setSsoState('error')
      setSsoError(e?.response?.data?.detail || e.message || 'Could not start SSO.')
    }
  }, [schedulePoll])

  const fireDistill = async () => {
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

  const handleDistill = async () => {
    if (!window.confirm('Trigger distill & clear? This will summarize session memory and reset token usage.')) return

    // Check for valid stored token first
    if (getStoredToken()) {
      await fireDistill()
    } else {
      // Need SSO — kick off device flow, distill fires automatically on success
      await startSso()
    }
  }

  const handleLogout = () => {
    clearToken()
    setSsoState(null)
    setSsoUser(null)
    stopPolling()
  }

  // Cleanup on unmount
  useEffect(() => () => stopPolling(), [])

  // ── SSO overlay panel ───────────────────────────────────────────────────────
  const renderSsoPanel = () => {
    if (!ssoState || ssoState === 'done') return null

    const isActive = ssoState === 'polling' || ssoState === 'pending_url'

    return (
      <div style={{
        ...CARD,
        borderColor: ssoState === 'error' ? '#ef4444' : '#e9456088',
        marginBottom: 20,
        position: 'relative',
      }}>
        {ssoState === 'checking' && (
          <p style={{ color: '#aaa', margin: 0 }}>⏳ Connecting to Auth0…</p>
        )}

        {isActive && ssoUrl && (
          <>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#fff', marginBottom: 12 }}>
              🔐 Login required to trigger distill
            </div>
            <p style={{ color: '#aaa', margin: '0 0 10px' }}>
              A browser tab has been opened. If it didn't open, use the link below:
            </p>
            <a
              href={ssoUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-block',
                background: '#1e1e3a',
                color: ACCENT,
                padding: '8px 14px',
                borderRadius: 8,
                border: `1px solid ${ACCENT}55`,
                fontSize: 13,
                wordBreak: 'break-all',
                marginBottom: 12,
                textDecoration: 'none',
              }}
            >
              {ssoUrl}
            </a>
            {ssoCode && (
              <div style={{ marginBottom: 12 }}>
                <span style={{ color: '#888', fontSize: 13 }}>Confirm code: </span>
                <span style={{
                  fontFamily: 'monospace',
                  fontSize: 18,
                  fontWeight: 700,
                  color: '#fbbf24',
                  letterSpacing: 3,
                }}>
                  {ssoCode}
                </span>
              </div>
            )}
            <p style={{ color: '#555', fontSize: 12, margin: 0 }}>
              ⏳ Waiting for you to authenticate… Distill will trigger automatically.
            </p>
          </>
        )}

        {ssoState === 'error' && (
          <>
            <div style={{ color: '#ef4444', fontWeight: 600, marginBottom: 8 }}>❌ {ssoError}</div>
            <button
              onClick={startSso}
              style={{
                background: '#4a0020', color: '#f87171',
                border: '1px solid #7f1d1d', borderRadius: 8,
                padding: '6px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
              }}
            >
              Retry SSO
            </button>
          </>
        )}

        {isActive && (
          <button
            onClick={() => { stopPolling(); setSsoState(null) }}
            style={{
              position: 'absolute', top: 12, right: 12,
              background: 'transparent', border: 'none',
              color: '#555', cursor: 'pointer', fontSize: 18,
            }}
          >✕</button>
        )}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff' }}>📊 Dashboard</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {ssoUser && (
            <span style={{ fontSize: 12, color: '#666' }}>
              {ssoUser.name || ssoUser.email}
              <button
                onClick={handleLogout}
                style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', fontSize: 11, marginLeft: 6 }}
                title="Log out"
              >logout</button>
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
            disabled={distilling || ssoState === 'polling' || ssoState === 'checking'}
            style={{
              background: distilling ? '#3a1a2a' : '#4a0020',
              color: distilling ? '#888' : '#f87171',
              border: '1px solid #7f1d1d', borderRadius: 8,
              padding: '6px 14px',
              cursor: (distilling || ssoState === 'polling') ? 'not-allowed' : 'pointer',
              fontSize: 13, fontWeight: 600,
            }}
          >
            {distilling ? '⏳ Distilling…'
              : (ssoState === 'polling' || ssoState === 'checking') ? '🔐 Authenticating…'
              : '🧹 Distill & Clear'}
          </button>
        </div>
      </div>

      {renderSsoPanel()}

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
        </>
      )}

      {!data && !error && (
        <div style={{ color: '#555', textAlign: 'center', paddingTop: 80, fontSize: 16 }}>Loading...</div>
      )}
    </div>
  )
}
