import React, { useState, useEffect, useCallback } from 'react'
import { getChunks, postSummarize } from '../api.js'

const ACCENT = '#e94560'
const CARD = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }

function scoreColor(score) {
  if (score >= 0.7) return '#22c55e'
  if (score >= 0.4) return '#f59e0b'
  return '#ef4444'
}

function fmt(val) {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'number') return val.toFixed(3)
  return val
}

function fmtDate(d) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

export default function Chunks() {
  const [chunks, setChunks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [showModal, setShowModal] = useState(false)

  // Filters
  const [minScore, setMinScore] = useState(0)
  const [source, setSource] = useState('')
  const [limit, setLimit] = useState(50)

  // Summarize modal state
  const [topPct, setTopPct] = useState(0.4)
  const [pushS3, setPushS3] = useState(false)
  const [sumResult, setSumResult] = useState(null)
  const [sumLoading, setSumLoading] = useState(false)

  const fetchChunks = useCallback(async () => {
    setLoading(true)
    try {
      const params = { min_score: minScore, limit, source }
      const data = await getChunks(params)
      setChunks(Array.isArray(data) ? data : data.chunks || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [minScore, limit, source])

  useEffect(() => { fetchChunks() }, [fetchChunks])

  const handleSummarize = async () => {
    setSumLoading(true)
    setSumResult(null)
    try {
      const res = await postSummarize({ top_pct: topPct, push_to_s3: pushS3, context_hint: '' })
      setSumResult(res)
    } catch (e) {
      setSumResult({ error: e.message })
    } finally {
      setSumLoading(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff' }}>🧩 Chunks</h1>
        <button
          onClick={() => { setShowModal(true); setSumResult(null) }}
          style={{
            background: ACCENT, color: '#fff', border: 'none', borderRadius: 8,
            padding: '8px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
          }}
        >
          ⚡ Run Summarize
        </button>
      </div>

      {/* Filter bar */}
      <div style={{ ...CARD, marginBottom: 20, display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Min Score: {minScore}</label>
          <input
            type="range" min={0} max={1} step={0.05} value={minScore}
            onChange={e => setMinScore(Number(e.target.value))}
            style={{ width: 140 }}
          />
        </div>
        <div>
          <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Source</label>
          <input
            value={source}
            onChange={e => setSource(e.target.value)}
            placeholder="filter by source..."
            style={{
              background: '#0f0f1a', border: '1px solid #333', color: '#e0e0e0',
              borderRadius: 6, padding: '6px 10px', fontSize: 13, width: 180,
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
          onClick={fetchChunks}
          style={{
            marginTop: 16, background: '#2a2a40', color: '#ccc', border: '1px solid #333',
            borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 13,
          }}
        >
          Apply
        </button>
      </div>

      {error && <div style={{ color: '#ef4444', marginBottom: 12 }}>⚠️ {error}</div>}
      {loading && <div style={{ color: '#555', marginBottom: 12 }}>Loading...</div>}

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #2a2a40', color: '#888', textAlign: 'left' }}>
              {['ID', 'Source', 'Idx', 'Tokens', 'Fact', 'Pref', 'Intent', 'Composite', 'Summ?', 'Created'].map(h => (
                <th key={h} style={{ padding: '8px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {chunks.map(c => (
              <React.Fragment key={c.id}>
                <tr
                  onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                  style={{
                    borderBottom: '1px solid #1e1e30',
                    cursor: 'pointer',
                    background: expanded === c.id ? '#1e1e38' : 'transparent',
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={e => { if (expanded !== c.id) e.currentTarget.style.background = '#1a1a2e' }}
                  onMouseLeave={e => { if (expanded !== c.id) e.currentTarget.style.background = 'transparent' }}
                >
                  <td style={{ padding: '8px 10px', color: '#666' }}>{c.id}</td>
                  <td style={{ padding: '8px 10px', color: '#60a5fa', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.source_label || '—'}</td>
                  <td style={{ padding: '8px 10px', color: '#aaa' }}>{c.chunk_index ?? '—'}</td>
                  <td style={{ padding: '8px 10px', color: '#e0e0e0' }}>{c.token_count ?? '—'}</td>
                  <td style={{ padding: '8px 10px', color: '#aaa' }}>{fmt(c.fact_score)}</td>
                  <td style={{ padding: '8px 10px', color: '#aaa' }}>{fmt(c.preference_score)}</td>
                  <td style={{ padding: '8px 10px', color: '#aaa' }}>{fmt(c.intent_score)}</td>
                  <td style={{ padding: '8px 10px', fontWeight: 600, color: scoreColor(c.composite_score) }}>{fmt(c.composite_score)}</td>
                  <td style={{ padding: '8px 10px', color: c.is_summarized ? '#22c55e' : '#555' }}>{c.is_summarized ? '✓' : '—'}</td>
                  <td style={{ padding: '8px 10px', color: '#666', whiteSpace: 'nowrap' }}>{fmtDate(c.created_at)}</td>
                </tr>
                {expanded === c.id && (
                  <tr>
                    <td colSpan={10} style={{ padding: '12px 16px', background: '#0f1020', borderBottom: '2px solid #2a2a40' }}>
                      <pre style={{
                        fontFamily: 'monospace', fontSize: 12, color: '#ccc',
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 300, overflowY: 'auto',
                      }}>
                        {c.content || '(no content)'}
                      </pre>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
            {!loading && chunks.length === 0 && (
              <tr>
                <td colSpan={10} style={{ padding: '40px', textAlign: 'center', color: '#444' }}>No chunks found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Summarize Modal */}
      {showModal && (
        <div style={{
          position: 'fixed', inset: 0, background: '#000a', display: 'flex',
          alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{ ...CARD, width: 400, position: 'relative' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, color: '#fff', marginBottom: 20 }}>⚡ Run Summarize</h2>
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 13, color: '#aaa', display: 'block', marginBottom: 6 }}>
                Top %: <strong style={{ color: '#fff' }}>{(topPct * 100).toFixed(0)}%</strong>
              </label>
              <input
                type="range" min={0.1} max={1} step={0.05} value={topPct}
                onChange={e => setTopPct(Number(e.target.value))}
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox" id="pushS3" checked={pushS3}
                onChange={e => setPushS3(e.target.checked)}
              />
              <label htmlFor="pushS3" style={{ fontSize: 13, color: '#aaa', cursor: 'pointer' }}>Push to S3</label>
            </div>
            {sumResult && (
              <div style={{
                background: sumResult.error ? '#3d0f0f' : '#0f3d20',
                border: `1px solid ${sumResult.error ? '#ef4444' : '#22c55e'}`,
                borderRadius: 8, padding: 12, marginBottom: 16,
                fontSize: 12, color: sumResult.error ? '#ef4444' : '#22c55e',
                maxHeight: 120, overflowY: 'auto',
              }}>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(sumResult, null, 2)}</pre>
              </div>
            )}
            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={handleSummarize}
                disabled={sumLoading}
                style={{
                  flex: 1, background: ACCENT, color: '#fff', border: 'none', borderRadius: 8,
                  padding: '10px', cursor: sumLoading ? 'not-allowed' : 'pointer', fontSize: 14, fontWeight: 600,
                  opacity: sumLoading ? 0.7 : 1,
                }}
              >
                {sumLoading ? 'Running...' : 'Run'}
              </button>
              <button
                onClick={() => setShowModal(false)}
                style={{
                  flex: 1, background: '#2a2a40', color: '#ccc', border: '1px solid #333',
                  borderRadius: 8, padding: '10px', cursor: 'pointer', fontSize: 14,
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
