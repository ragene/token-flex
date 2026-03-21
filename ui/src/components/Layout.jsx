import React from 'react'
import { Outlet, NavLink } from 'react-router-dom'

const SIDEBAR = '#1a1a2e'
const ACCENT = '#e94560'

const navItems = [
  { to: '/dashboard', label: '📊 Dashboard' },
  { to: '/chunks', label: '🧩 Chunks' },
  { to: '/summaries', label: '📝 Summaries' },
  { to: '/ingest', label: '📥 Ingest' },
]

export default function Layout() {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <nav style={{
        width: 220,
        background: SIDEBAR,
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
        borderRight: `2px solid ${ACCENT}22`,
      }}>
        <div style={{
          padding: '24px 16px 16px',
          borderBottom: `1px solid ${ACCENT}33`,
        }}>
          <div style={{ color: ACCENT, fontWeight: 700, fontSize: 18, letterSpacing: 1 }}>
            🔁 Token Flow
          </div>
          <div style={{ color: '#888', fontSize: 12, marginTop: 4 }}>Smart Memory Dashboard</div>
        </div>
        <div style={{ flex: 1, paddingTop: 16 }}>
          {navItems.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              style={({ isActive }) => ({
                display: 'block',
                padding: '12px 20px',
                color: isActive ? ACCENT : '#ccc',
                textDecoration: 'none',
                fontSize: 14,
                fontWeight: isActive ? 600 : 400,
                background: isActive ? `${ACCENT}18` : 'transparent',
                borderLeft: isActive ? `3px solid ${ACCENT}` : '3px solid transparent',
                transition: 'all 0.15s',
              })}
            >
              {label}
            </NavLink>
          ))}
        </div>
        <div style={{ padding: '16px', color: '#444', fontSize: 11, borderTop: `1px solid ${ACCENT}22` }}>
          token-flow v0.1
        </div>
      </nav>

      {/* Content */}
      <main style={{ flex: 1, padding: '32px', overflowY: 'auto', background: '#0f0f1a' }}>
        <Outlet />
      </main>
    </div>
  )
}
