import React from 'react'
import { useAuth0 } from '@auth0/auth0-react'

const SIDEBAR = '#1a1a2e'
const ACCENT = '#e94560'

const domain = import.meta.env.VITE_AUTH0_DOMAIN
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID
const configured = !!(domain && clientId)

// Inner component that uses useAuth0 — only rendered when Auth0Provider is present
function Auth0LoginButton() {
  const { loginWithRedirect } = useAuth0()
  return (
    <button
      onClick={() => loginWithRedirect()}
      style={{
        width: '100%',
        padding: '14px 24px',
        background: ACCENT,
        color: '#fff',
        border: 'none',
        borderRadius: 8,
        fontSize: 15,
        fontWeight: 600,
        cursor: 'pointer',
        letterSpacing: 0.5,
        transition: 'opacity 0.15s',
      }}
      onMouseOver={e => e.currentTarget.style.opacity = '0.85'}
      onMouseOut={e => e.currentTarget.style.opacity = '1'}
    >
      Sign in with Auth0
    </button>
  )
}

function NotConfiguredWarning() {
  return (
    <div style={{
      background: '#2a1a1a',
      border: `1px solid ${ACCENT}66`,
      borderRadius: 8,
      padding: '16px',
      color: '#ffaa44',
      fontSize: 13,
      lineHeight: 1.6,
    }}>
      ⚠️ Auth0 is not configured.{' '}
      Set <code style={{ color: ACCENT }}>VITE_AUTH0_DOMAIN</code> and{' '}
      <code style={{ color: ACCENT }}>VITE_AUTH0_CLIENT_ID</code> to enable login.
    </div>
  )
}

export default function Login() {
  return (
    <div style={{
      minHeight: '100vh',
      background: '#0f0f1a',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <div style={{
        background: SIDEBAR,
        border: `1px solid ${ACCENT}44`,
        borderRadius: 12,
        padding: '48px 40px',
        width: 360,
        textAlign: 'center',
        boxShadow: `0 8px 32px ${ACCENT}22`,
      }}>
        <div style={{ fontSize: 48, marginBottom: 12 }}>🔁</div>
        <h1 style={{
          color: ACCENT,
          fontSize: 24,
          fontWeight: 700,
          margin: '0 0 8px',
          letterSpacing: 1,
        }}>
          Token Flow
        </h1>
        <p style={{
          color: '#888',
          fontSize: 14,
          margin: '0 0 36px',
        }}>
          Smart Memory Dashboard
        </p>

        {configured ? <Auth0LoginButton /> : <NotConfiguredWarning />}

        {configured && (
          <div style={{ textAlign: 'center', color: '#666', fontSize: 13, marginTop: 12 }}>
            New user? Sign in with Auth0 above to request access.
            <br />
            <span style={{ color: '#aaa' }}>An admin will activate your account.</span>
          </div>
        )}
      </div>
    </div>
  )
}
