import React from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import { useMe } from '../hooks/useMe.js'

const SIDEBAR = '#1a1a2e'
const ACCENT = '#e94560'

const domain = import.meta.env.VITE_AUTH0_DOMAIN
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID
const auth0Configured = !!(domain && clientId)

// minRole: 'viewer' = everyone, 'admin' = admins only
const NAV_ITEMS = [
  { to: '/dashboard',  label: '📊 Dashboard',   minRole: 'viewer' },
  { to: '/token-data', label: '🔢 Token Data',   minRole: 'viewer' },
  { to: '/memory',     label: '🧠 Memory',       minRole: 'viewer' },
  { to: '/activity',   label: '⚡ Activity',     minRole: 'viewer' },
  { to: '/chunks',     label: '🧩 Chunks',       minRole: 'viewer' },
  { to: '/summaries',  label: '📝 Summaries',    minRole: 'viewer' },
  { to: '/ingest',     label: '📥 Ingest',       minRole: 'admin'  },
  { to: '/sessions',   label: '🖥️ Sessions',     minRole: 'admin'  },
  { to: '/users',      label: '👥 Users',        minRole: 'admin'  },
]

const ROLE_RANK = { viewer: 0, admin: 1 }
function canAccess(userRole, minRole) {
  return (ROLE_RANK[userRole] ?? 0) >= (ROLE_RANK[minRole] ?? 0)
}

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
  const { role, name, email } = useMe()
  const visibleNav = NAV_ITEMS.filter(({ minRole }) => canAccess(role, minRole))

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
          {visibleNav.map(({ to, label }) => (
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
          {/* User identity */}
          {email && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 12, color: '#ccc', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {name}
              </div>
              <div style={{ fontSize: 10, color: '#555', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {email}
              </div>
              <span style={{
                display: 'inline-block', marginTop: 4,
                padding: '1px 7px', borderRadius: 8, fontSize: 10, fontWeight: 600,
                background: role === 'admin' ? `${ACCENT}22` : '#1e1e38',
                color: role === 'admin' ? ACCENT : '#555',
                border: `1px solid ${role === 'admin' ? ACCENT + '44' : '#2a2a40'}`,
              }}>
                {role || 'viewer'}
              </span>
            </div>
          )}
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
