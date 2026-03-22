import React from 'react'

const ACCENT = '#e94560'

export default function TokenMeter({ total, warn, distill }) {
  const max = Math.max(distill || warn || 50000, total) * 1.1
  const warnPct = Math.min(100, ((warn || 0) / max) * 100)
  const distillPct = Math.min(100, ((distill || 0) / max) * 100)
  const usedPct = Math.min(100, (total / max) * 100)

  const barColor = total >= distill
    ? '#ef4444'
    : total >= warn
    ? '#f59e0b'
    : '#22c55e'

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 12, color: '#888' }}>
        <span>0</span>
        <span style={{ color: '#f59e0b' }}>warn: {(warn || 0).toLocaleString()}</span>
        <span style={{ color: '#ef4444' }}>distill: {(distill || 0).toLocaleString()}</span>
        <span>{Math.round(max).toLocaleString()}</span>
      </div>
      <div style={{
        position: 'relative',
        height: 20,
        background: '#1e1e30',
        borderRadius: 10,
        overflow: 'hidden',
        border: '1px solid #333',
      }}>
        {/* Used bar */}
        <div style={{
          position: 'absolute',
          left: 0,
          top: 0,
          height: '100%',
          width: `${usedPct}%`,
          background: barColor,
          borderRadius: 10,
          transition: 'width 0.5s ease',
        }} />
        {/* Warn marker */}
        {warn > 0 && (
          <div style={{
            position: 'absolute',
            left: `${warnPct}%`,
            top: 0,
            height: '100%',
            width: 2,
            background: '#f59e0b',
            opacity: 0.8,
          }} />
        )}
        {/* Distill marker */}
        {distill > 0 && distill !== warn && (
          <div style={{
            position: 'absolute',
            left: `${distillPct}%`,
            top: 0,
            height: '100%',
            width: 2,
            background: '#ef4444',
            opacity: 0.8,
          }} />
        )}
      </div>
      <div style={{ marginTop: 6, fontSize: 13, color: '#aaa', textAlign: 'center' }}>
        <strong style={{ color: barColor }}>{total.toLocaleString()}</strong> tokens used
        {' '}({usedPct.toFixed(1)}% of warn threshold)
      </div>
    </div>
  )
}
