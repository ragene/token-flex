import React, { useState, useEffect, useCallback } from 'react'
import { getSummaries, getTokenSummary } from '../api.js'

const ACCENT = '#e94560'
const CARD = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }

function fmt(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

function fmtCost(n) {
  if (n == null) return '—'
  return `$${Number(n).toFixed(4)}`
}

function HaikuStats({ stats }) {
  if (!stats) return null
  const { rows = [], grand_total_tokens, grand_total_calls, grand_cost_usd } = stats

  return (
    <div style={{ ...CARD, marginBottom: 24 }}>
      <div style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>🤖 Haiku Summary Stats</div>

      {/* Grand totals */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 20 }}>
        {[
          { label: 'Total Calls',  value: fmt(grand_total_calls),  color: '#60a5fa' },
          { label: 'Total Tokens', value: fmt(grand_total_tokens), color: '#a78bfa' },
          { label: 'Total Cost',   value: fmtCost(grand_cost_usd), color: '#34d399' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ background: '#0f0f1a', borderRadius: 8, padding: '12px 18px', border: '1px solid #2a2a40', minWidth: 140 }}>
            <div style={{ fontSize: 11, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>{label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Per-operation table */}
      {rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #2a2a40', color: '#666', textAlign: 'left' }}>
                {['Operation', 'Model', 'Calls', 'Prompt', 'Completion', 'Total', 'Cost'].map(h => (
                  <th key={h} style={{ padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #1e1e30' }}>
                  <td style={{ padding: '6px 10px', color: '#e0e0e0' }}>{r.operation || '—'}</td>
                  <td style={{ padding: '6px 10px', color: '#a78bfa' }}>{r.model || '—'}</td>
                  <td style={{ padding: '6px 10px', color: '#60a5fa', textAlign: 'right' }}>{fmt(r.total_calls)}</td>
                  <td style={{ padding: '6px 10px', color: '#888', textAlign: 'right' }}>{fmt(r.prompt_tokens)}</td>
                  <td style={{ padding: '6px 10px', color: '#888', textAlign: 'right' }}>{fmt(r.completion_tokens)}</td>
                  <td style={{ padding: '6px 10px', color: '#a78bfa', fontWeight: 600, textAlign: 'right' }}>{fmt(r.total_tokens)}</td>
                  <td style={{ padding: '6px 10px', color: '#34d399', textAlign: 'right' }}>{fmtCost(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.length === 0 && (
        <div style={{ color: '#444', fontSize: 13 }}>No Haiku calls recorded yet.</div>
      )}
    </div>
  )
}

function scoreColor(score) {
  if (score >= 0.7) return '#22c55e'
  if (score >= 0.4) return '#f59e0b'
  return '#ef4444'
}

function fmtDate(d) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

function truncate(str, n = 80) {
  if (!str) return '—'
  return str.length > n ? str.slice(0, n) + '…' : str
}

export default function Summaries() {
  const [summaries, setSummaries] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [source, setSource] = useState('')
  const [limit, setLimit] = useState(50)
  const [haikuStats, setHaikuStats] = useState(null)

  const fetchSummaries = useCallback(async () => {
    setLoading(true)
    try {
      const [data, stats] = await Promise.all([
        getSummaries({ source, limit }),
        getTokenSummary().catch(() => null),
      ])
      setSummaries(Array.isArray(data) ? data : data.summaries || [])
      setHaikuStats(stats)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [source, limit])

  useEffect(() => { fetchSummaries() }, [fetchSummaries])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff' }}>📝 Summaries</h1>
        <button
          onClick={fetchSummaries}
          style={{
            background: ACCENT, color: '#fff', border: 'none', borderRadius: 8,
            padding: '6px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
          }}
        >
          ↻ Refresh
        </button>
      </div>

      {/* Filter bar */}
      <div style={{ ...CARD, marginBottom: 20, display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Source</label>
          <input
            value={source}
            onChange={e => setSource(e.target.value)}
            placeholder="filter by source..."
            style={{
              background: '#0f0f1a', border: '1px solid #333', color: '#e0e0e0',
              borderRadius: 6, padding: '6px 10px', fontSize: 13, width: 200,
            }}
          />
        </div>
        <div>
          <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Limit</label>
          <select
            value={limit}
            onChange={e => setLimit(Number(e.target.value))}
            style={{
              background: '#0f0f1a', border: '1px solid #333', color: '#e0e0e0',
              borderRadius: 6, padding: '6px 10px', fontSize: 13,
            }}
          >
            {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <button
          onClick={fetchSummaries}
          style={{
            marginTop: 16, background: '#2a2a40', color: '#ccc', border: '1px solid #333',
            borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 13,
          }}
        >
          Apply
        </button>
      </div>

      <HaikuStats stats={haikuStats} />

      {error && <div style={{ color: '#ef4444', marginBottom: 12 }}>⚠️ {error}</div>}
      {loading && <div style={{ color: '#555', marginBottom: 12 }}>Loading...</div>}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #2a2a40', color: '#888', textAlign: 'left' }}>
              {['ID', 'Source', 'Idx', 'Score', 'Summary', 'S3', 'Created'].map(h => (
                <th key={h} style={{ padding: '8px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {summaries.map(s => (
              <React.Fragment key={s.id}>
                <tr
                  onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                  style={{
                    borderBottom: '1px solid #1e1e30',
                    cursor: 'pointer',
                    background: expanded === s.id ? '#1e1e38' : 'transparent',
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={e => { if (expanded !== s.id) e.currentTarget.style.background = '#1a1a2e' }}
                  onMouseLeave={e => { if (expanded !== s.id) e.currentTarget.style.background = 'transparent' }}
                >
                  <td style={{ padding: '8px 10px', color: '#666' }}>{s.id}</td>
                  <td style={{ padding: '8px 10px', color: '#60a5fa', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.source_label || '—'}</td>
                  <td style={{ padding: '8px 10px', color: '#aaa' }}>{s.chunk_index ?? '—'}</td>
                  <td style={{ padding: '8px 10px', fontWeight: 600, color: scoreColor(s.composite_score) }}>
                    {s.composite_score != null ? s.composite_score.toFixed(3) : '—'}
                  </td>
                  <td style={{ padding: '8px 10px', color: '#ccc', maxWidth: 300 }}>{truncate(s.summary)}</td>
                  <td style={{ padding: '8px 10px', color: s.pushed_to_s3_at ? '#22c55e' : '#555' }}>
                    {s.pushed_to_s3_at ? fmtDate(s.pushed_to_s3_at) : '—'}
                  </td>
                  <td style={{ padding: '8px 10px', color: '#666', whiteSpace: 'nowrap' }}>{fmtDate(s.created_at)}</td>
                </tr>
                {expanded === s.id && (
                  <tr>
                    <td colSpan={7} style={{ padding: '12px 16px', background: '#0f1020', borderBottom: '2px solid #2a2a40' }}>
                      <pre style={{
                        fontFamily: 'monospace', fontSize: 12, color: '#ccc',
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 300, overflowY: 'auto',
                      }}>
                        {s.summary || '(no content)'}
                      </pre>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
            {!loading && summaries.length === 0 && (
              <tr>
                <td colSpan={7} style={{ padding: '40px', textAlign: 'center', color: '#444' }}>No summaries found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
