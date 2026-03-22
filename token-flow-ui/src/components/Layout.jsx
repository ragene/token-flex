import React from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'

const SIDEBAR = '#1a1a2e'
const ACCENT = '#e94560'

const domain = import.meta.env.VITE_AUTH0_DOMAIN
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID
const auth0Configured = !!(domain && clientId)

const navItems = [
  { to: '/dashboard',  label: '📊 Dashboard' },
  { to: '/token-data', label: '🔢 Token Data' },
  { to: '/memory',     label: '🧠 Memory' },
  { to: '/activity',   label: '⚡ Activity' },
  { to: '/chunks',     label: '🧩 Chunks' },
  { to: '/summaries',  label: '📝 Summaries' },
  { to: '/ingest',     label: '📥 Ingest' },
  { to: '/users',      label: '👥 Users' },
]

// Logout button — only rendered when Auth0Provider is present
function Auth0LogoutButton() {
  const { logout } = useAuth0()
  const handleLogout = () => {
    localStorage.removeItem('tf_token')
    logout({ logoutParams: { returnTo: window.location.origin + '/login' } })
  }
  return <LogoutButton onClick={handleLogout} />
}

// Fallback logout button when Auth0 not configured
function PlainLogoutButton() {
  const navigate = useNavigate()
  const handleLogout = () => {
    localStorage.removeItem('tf_token')
    navigate('/login', { replace: true })
  }
  return <LogoutButton onClick={handleLogout} />
}

function LogoutButton({ onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: '100%',
        padding: '10px 12px',
        background: 'transparent',
        border: `1px solid ${ACCENT}33`,
        borderRadius: 6,
        color: '#666',
        fontSize: 13,
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'all 0.15s',
      }}
      onMouseOver={e => {
        e.currentTarget.style.color = '#ccc'
        e.currentTarget.style.borderColor = `${ACCENT}66`
      }}
      onMouseOut={e => {
        e.currentTarget.style.color = '#666'
        e.currentTarget.style.borderColor = `${ACCENT}33`
      }}
    >
      🚪 Sign out
    </button>
  )
}

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
        <div style={{ padding: '16px', borderTop: `1px solid ${ACCENT}22` }}>
          {auth0Configured ? <Auth0LogoutButton /> : <PlainLogoutButton />}
          <div style={{ color: '#333', fontSize: 11, marginTop: 8, textAlign: 'center' }}>
            token-flow v0.1
          </div>
        </div>
      </nav>

      {/* Content */}
      <main style={{ flex: 1, padding: '32px', overflowY: 'auto', background: '#0f0f1a' }}>
        <Outlet />
      </main>
    </div>
  )
}
