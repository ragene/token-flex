import React, { useState, useEffect, useCallback } from 'react'
import { getSummaries, postSummarize } from '../api.js'

const ACCENT = '#e94560'
const CARD = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40' }

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

  // Summarize pipeline
  const [sumLoading, setSumLoading] = useState(false)
  const [sumResult, setSumResult] = useState(null)
  const [sumError, setSumError] = useState(null)
  const [topPct, setTopPct] = useState(0.4)
  const [pushToS3, setPushToS3] = useState(false)

  const handleSummarize = async () => {
    setSumLoading(true)
    setSumResult(null)
    setSumError(null)
    try {
      const res = await postSummarize({ top_pct: topPct, push_to_s3: pushToS3, context_hint: '' })
      setSumResult(res)
      fetchSummaries()
    } catch (e) {
      setSumError(e.message)
    } finally {
      setSumLoading(false)
    }
  }

  const fetchSummaries = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getSummaries({ source, limit })
      setSummaries(Array.isArray(data) ? data : data.summaries || [])
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

      {/* Run Summarizer */}
      <div style={{ ...CARD, marginBottom: 20 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#fff', marginBottom: 14 }}>⚙️ Run Summarizer</h2>
        <div style={{ display: 'flex', gap: 24, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Top % of chunks</label>
            <select
              value={topPct}
              onChange={e => setTopPct(Number(e.target.value))}
              style={{ background: '#0f0f1a', border: '1px solid #333', color: '#e0e0e0', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
            >
              {[0.2, 0.4, 0.6, 0.8, 1.0].map(v => (
                <option key={v} value={v}>{Math.round(v * 100)}%</option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingBottom: 2 }}>
            <input
              type="checkbox"
              id="pushS3"
              checked={pushToS3}
              onChange={e => setPushToS3(e.target.checked)}
              style={{ accentColor: ACCENT }}
            />
            <label htmlFor="pushS3" style={{ fontSize: 13, color: '#ccc', cursor: 'pointer' }}>Push to S3</label>
          </div>
          <button
            onClick={handleSummarize}
            disabled={sumLoading}
            style={{
              background: sumLoading ? '#333' : ACCENT,
              color: '#fff', border: 'none', borderRadius: 8,
              padding: '8px 18px', cursor: sumLoading ? 'not-allowed' : 'pointer',
              fontSize: 13, fontWeight: 600, opacity: sumLoading ? 0.6 : 1,
            }}
          >
            {sumLoading ? 'Running...' : '▶ Run Summarize'}
          </button>
        </div>
        {sumResult && (
          <div style={{ marginTop: 10, fontSize: 13, color: '#22c55e' }}>
            ✅ Summarized <strong>{sumResult.summarized}</strong> chunks
            {sumResult.pushed > 0 && <>, pushed <strong>{sumResult.pushed}</strong> to S3</>}.
          </div>
        )}
        {sumError && <div style={{ marginTop: 10, fontSize: 13, color: '#ef4444' }}>⚠️ {sumError}</div>}
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
