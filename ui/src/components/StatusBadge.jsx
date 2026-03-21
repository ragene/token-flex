import React from 'react'

const STATUS_COLORS = {
  ok: { bg: '#0f3d20', border: '#22c55e', text: '#22c55e' },
  warn: { bg: '#3d2f0f', border: '#f59e0b', text: '#f59e0b' },
  critical: { bg: '#3d0f0f', border: '#ef4444', text: '#ef4444' },
  error: { bg: '#3d0f0f', border: '#ef4444', text: '#ef4444' },
}

export default function StatusBadge({ status }) {
  const s = STATUS_COLORS[status] || STATUS_COLORS.ok
  return (
    <span style={{
      display: 'inline-block',
      padding: '3px 10px',
      borderRadius: 12,
      border: `1px solid ${s.border}`,
      background: s.bg,
      color: s.text,
      fontSize: 12,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
    }}>
      {status}
    </span>
  )
}
