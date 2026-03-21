import React, { useState } from 'react'
import { postIngest, postAutoIngest, postMemoryQuery } from '../api.js'

const ACCENT = '#e94560'
const CARD = { background: '#16162a', borderRadius: 12, padding: 24, border: '1px solid #2a2a40', marginBottom: 24 }

function ResultBox({ data, error }) {
  if (!data && !error) return null
  const isError = !!error
  return (
    <div style={{
      marginTop: 12,
      background: isError ? '#3d0f0f' : '#0f3d20',
      border: `1px solid ${isError ? '#ef4444' : '#22c55e'}`,
      borderRadius: 8,
      padding: 12,
      fontSize: 12,
      color: isError ? '#ef4444' : '#22c55e',
      maxHeight: 200,
      overflowY: 'auto',
    }}>
      <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(error || data, null, 2)}</pre>
    </div>
  )
}

function scoreColor(score) {
  if (score >= 0.7) return '#22c55e'
  if (score >= 0.4) return '#f59e0b'
  return '#ef4444'
}

export default function Ingest() {
  // Manual ingest
  const [content, setContent] = useState('')
  const [sourceLabel, setSourceLabel] = useState('manual')
  const [ingestLoading, setIngestLoading] = useState(false)
  const [ingestResult, setIngestResult] = useState(null)
  const [ingestError, setIngestError] = useState(null)

  // Auto ingest
  const [autoLoading, setAutoLoading] = useState(false)
  const [autoResult, setAutoResult] = useState(null)
  const [autoError, setAutoError] = useState(null)

  // Query memory
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(5)
  const [queryLoading, setQueryLoading] = useState(false)
  const [queryResults, setQueryResults] = useState(null)
  const [queryError, setQueryError] = useState(null)

  const handleIngest = async () => {
    if (!content.trim()) return
    setIngestLoading(true)
    setIngestResult(null)
    setIngestError(null)
    try {
      const res = await postIngest({ content, source_label: sourceLabel, metadata: {} })
      setIngestResult(res)
    } catch (e) {
      setIngestError(e.message)
    } finally {
      setIngestLoading(false)
    }
  }

  const handleAutoIngest = async () => {
    setAutoLoading(true)
    setAutoResult(null)
    setAutoError(null)
    try {
      const res = await postAutoIngest()
      setAutoResult(res)
    } catch (e) {
      setAutoError(e.message)
    } finally {
      setAutoLoading(false)
    }
  }

  const handleQuery = async () => {
    if (!query.trim()) return
    setQueryLoading(true)
    setQueryResults(null)
    setQueryError(null)
    try {
      const res = await postMemoryQuery({ query, top_k: topK })
      setQueryResults(res)
    } catch (e) {
      setQueryError(e.message)
    } finally {
      setQueryLoading(false)
    }
  }

  const btnStyle = (disabled) => ({
    background: disabled ? '#333' : ACCENT,
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '10px 20px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    fontSize: 14,
    fontWeight: 600,
    opacity: disabled ? 0.6 : 1,
  })

  const inputStyle = {
    background: '#0f0f1a',
    border: '1px solid #333',
    color: '#e0e0e0',
    borderRadius: 6,
    padding: '8px 12px',
    fontSize: 13,
    width: '100%',
  }

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', marginBottom: 24 }}>📥 Ingest</h1>

      {/* Manual Ingest */}
      <div style={CARD}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>✍️ Manual Ingest</h2>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Source Label</label>
          <input
            value={sourceLabel}
            onChange={e => setSourceLabel(e.target.value)}
            style={{ ...inputStyle, width: 220 }}
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Content</label>
          <textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="Paste content to ingest into memory..."
            rows={6}
            style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
          />
        </div>
        <button
          onClick={handleIngest}
          disabled={ingestLoading || !content.trim()}
          style={btnStyle(ingestLoading || !content.trim())}
        >
          {ingestLoading ? 'Ingesting...' : '📥 Ingest'}
        </button>
        <ResultBox data={ingestResult} error={ingestError} />
      </div>

      {/* Auto Ingest */}
      <div style={CARD}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 8 }}>🤖 Memory Auto-Ingest</h2>
        <p style={{ fontSize: 13, color: '#888', marginBottom: 16 }}>
          Trigger automatic ingestion of session context into memory.
        </p>
        <button
          onClick={handleAutoIngest}
          disabled={autoLoading}
          style={btnStyle(autoLoading)}
        >
          {autoLoading ? 'Running...' : '⚡ Run Auto-Ingest'}
        </button>
        <ResultBox data={autoResult} error={autoError} />
      </div>

      {/* Query Memory */}
      <div style={CARD}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: '#fff', marginBottom: 16 }}>🔍 Query Memory</h2>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Query</label>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleQuery()}
              placeholder="Search memory..."
              style={inputStyle}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#888', display: 'block', marginBottom: 4 }}>Top K</label>
            <select
              value={topK}
              onChange={e => setTopK(Number(e.target.value))}
              style={{ ...inputStyle, width: 80 }}
            >
              {[3, 5, 10, 20].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <button
            onClick={handleQuery}
            disabled={queryLoading || !query.trim()}
            style={btnStyle(queryLoading || !query.trim())}
          >
            {queryLoading ? 'Searching...' : '🔍 Search'}
          </button>
        </div>

        {queryError && (
          <div style={{ color: '#ef4444', fontSize: 13, marginTop: 8 }}>⚠️ {queryError}</div>
        )}

        {queryResults && (
          <div style={{ marginTop: 16 }}>
            {Array.isArray(queryResults.results) && queryResults.results.length > 0 ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #2a2a40', color: '#888', textAlign: 'left' }}>
                    {['#', 'Source', 'Score', 'Excerpt'].map(h => (
                      <th key={h} style={{ padding: '8px 10px', fontWeight: 600 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {queryResults.results.map((r, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #1e1e30' }}>
                      <td style={{ padding: '8px 10px', color: '#666' }}>{i + 1}</td>
                      <td style={{ padding: '8px 10px', color: '#60a5fa' }}>{r.source_label || '—'}</td>
                      <td style={{ padding: '8px 10px', fontWeight: 600, color: scoreColor(r.score ?? r.composite_score) }}>
                        {(r.score ?? r.composite_score ?? 0).toFixed(3)}
                      </td>
                      <td style={{ padding: '8px 10px', color: '#ccc', maxWidth: 400 }}>
                        {r.content ? r.content.slice(0, 120) + (r.content.length > 120 ? '…' : '') : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div style={{
                background: '#0f3d20', border: '1px solid #22c55e', borderRadius: 8,
                padding: 12, fontSize: 12, color: '#22c55e',
              }}>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(queryResults, null, 2)}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
