import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth0 } from '@auth0/auth0-react'
import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || ''
const ACCENT = '#e94560'

export default function PendingAccess() {
  const navigate = useNavigate()
  const { getAccessTokenSilently, logout } = useAuth0()
  const [checking, setChecking] = useState(false)
  const [msg, setMsg] = useState('')

  async function checkAccess() {
    setChecking(true)
    setMsg('')
    try {
      const auth0Token = await getAccessTokenSilently({ authorizationParams: { audience: undefined } })
        .catch(() => getAccessTokenSilently())
      const { data } = await axios.post(
        `${BASE_URL}/auth/exchange`,
        {},
        { headers: { Authorization: `Bearer ${auth0Token}` } }
      )
      localStorage.setItem('tf_token', data.access_token)
      navigate('/dashboard', { replace: true })
    } catch (e) {
      if (e?.response?.status === 403) {
        setMsg("Still pending — your admin hasn't activated your account yet.")
      } else {
        setMsg('Something went wrong. Try signing out and back in.')
      }
    } finally {
      setChecking(false)
    }
  }

  function handleLogout() {
    localStorage.removeItem('tf_token')
    logout({ logoutParams: { returnTo: window.location.origin + '/login' } })
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center',
      minHeight: '100vh', background: '#0f0f1a', color: '#fff', padding: 24,
    }}>
      <div style={{
        background: '#1a1a2e', borderRadius: 12, padding: '40px 36px',
        maxWidth: 440, width: '100%', textAlign: 'center',
        boxShadow: '0 8px 32px rgba(0,0,0,0.5)', border: '1px solid #e9456022',
      }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>⏳</div>
        <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 10, color: '#fff' }}>
          Access Pending
        </h2>
        <p style={{ color: '#aaa', fontSize: 15, lineHeight: 1.6, marginBottom: 24 }}>
          Your account is <strong style={{ color: '#fff' }}>pending admin approval</strong>.
          <br /><br />
          An admin has been notified. You'll have access once your account is activated in the 👥 Users panel.
        </p>

        {msg && (
          <div style={{
            background: msg.includes('Still') ? '#2a2000' : '#2a0010',
            border: `1px solid ${msg.includes('Still') ? '#ffc10744' : '#e9456044'}`,
            borderRadius: 6, padding: '10px 14px', fontSize: 13,
            color: msg.includes('Still') ? '#ffc107' : ACCENT,
            marginBottom: 16,
          }}>
            {msg}
          </div>
        )}

        <button onClick={checkAccess} disabled={checking} style={{
          width: '100%', padding: '12px', background: '#2a2a4a',
          color: '#fff', border: '1px solid #444', borderRadius: 6,
          fontSize: 15, fontWeight: 700, cursor: checking ? 'not-allowed' : 'pointer',
          marginBottom: 10, opacity: checking ? 0.6 : 1,
        }}>
          {checking ? 'Checking...' : '🔄 Check for Access'}
        </button>

        <button onClick={handleLogout} style={{
          width: '100%', padding: '12px', background: ACCENT,
          color: '#fff', border: 'none', borderRadius: 6,
          fontSize: 15, fontWeight: 700, cursor: 'pointer',
        }}>
          Sign Out
        </button>
      </div>
    </div>
  )
}
