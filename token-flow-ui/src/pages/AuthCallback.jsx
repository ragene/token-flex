import React, { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import axios from 'axios'

const ACCENT = '#e94560'
const BASE_URL = import.meta.env.VITE_API_URL || ''

export default function AuthCallback() {
  const { isLoading, isAuthenticated, error, getAccessTokenSilently } = useAuth0()
  const navigate = useNavigate()
  const attempted = useRef(false)

  useEffect(() => {
    if (isLoading || attempted.current) return

    if (error) {
      const t = setTimeout(() => navigate('/login'), 3000)
      return () => clearTimeout(t)
    }

    if (isAuthenticated) {
      attempted.current = true
      // Try without audience first (returns opaque token usable for /userinfo),
      // fall back to default getAccessTokenSilently
      getAccessTokenSilently({ authorizationParams: { audience: undefined } })
        .catch(() => getAccessTokenSilently())
        .then(async (auth0Token) => {
          const { data } = await axios.post(
            `${BASE_URL}/auth/exchange`,
            {},
            { headers: { Authorization: `Bearer ${auth0Token}` } }
          )
          localStorage.setItem('tf_token', data.access_token)
          navigate('/dashboard', { replace: true })
        })
        .catch(err => {
          console.error('Exchange failed:', err)
          if (err?.response?.status === 403) {
            navigate('/pending-access', { replace: true })
          } else {
            setTimeout(() => navigate('/login'), 3000)
          }
        })
    }
  }, [isLoading, isAuthenticated, error, getAccessTokenSilently, navigate])

  if (error) {
    return (
      <div style={{
        minHeight: '100vh',
        background: '#0f0f1a',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexDirection: 'column',
        gap: 16,
        color: '#fff',
      }}>
        <div style={{ fontSize: 32 }}>❌</div>
        <div style={{ color: ACCENT, fontWeight: 600 }}>Authentication failed</div>
        <div style={{ color: '#888', fontSize: 14 }}>{error.message}</div>
        <div style={{ color: '#555', fontSize: 13 }}>Redirecting to login...</div>
      </div>
    )
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: '#0f0f1a',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexDirection: 'column',
      gap: 16,
      color: '#fff',
    }}>
      <div style={{
        width: 40,
        height: 40,
        border: `3px solid ${ACCENT}44`,
        borderTop: `3px solid ${ACCENT}`,
        borderRadius: '50%',
        animation: 'spin 0.8s linear infinite',
      }} />
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <div style={{ color: '#aaa', fontSize: 15 }}>Signing you in...</div>
    </div>
  )
}
