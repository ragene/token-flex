import React, { useEffect, useState, useCallback } from 'react'
import { getUsers, patchUserRole, activateUser, deactivateUser, deleteUser } from '../api'

const BG = '#0f0f1a'
const CARD = '#1a1a2e'
const ACCENT = '#e94560'
const TEXT = '#e0e0e0'
const MUTED = '#888'

function getCurrentUser() {
  try {
    const token = localStorage.getItem('tf_token')
    const payload = JSON.parse(atob(token?.split('.')[1] || 'e30='))
    return payload
  } catch {
    return {}
  }
}

function Badge({ active }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 10px',
      borderRadius: 12,
      fontSize: 12,
      fontWeight: 600,
      background: active ? '#1a3a2a' : '#2a1a1a',
      color: active ? '#4caf8a' : '#e94560',
      border: `1px solid ${active ? '#4caf8a44' : '#e9456044'}`,
    }}>
      {active ? 'Active' : 'Inactive'}
    </span>
  )
}

function ActionBtn({ onClick, color, children, disabled }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseOver={() => setHover(true)}
      onMouseOut={() => setHover(false)}
      style={{
        padding: '4px 10px',
        fontSize: 12,
        borderRadius: 5,
        border: `1px solid ${color}`,
        background: hover && !disabled ? color + '22' : 'transparent',
        color: disabled ? '#444' : color,
        borderColor: disabled ? '#444' : color,
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'all 0.15s',
        marginLeft: 4,
      }}
    >
      {children}
    </button>
  )
}

export default function Users() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState({})
  const me = getCurrentUser()

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getUsers()
      setUsers(data)
    } catch (e) {
      if (e?.response?.status === 403) {
        setError('Admin access required to view users.')
      } else {
        setError(e?.response?.data?.detail || e.message || 'Failed to load users')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const withBusy = async (key, fn) => {
    setBusy(b => ({ ...b, [key]: true }))
    try {
      await fn()
      await load()
    } catch (e) {
      alert(e?.response?.data?.detail || e.message || 'Action failed')
    } finally {
      setBusy(b => ({ ...b, [key]: false }))
    }
  }

  const pendingCount = users.filter(u => !u.is_active).length

  const headStyle = {
    padding: '12px 16px',
    textAlign: 'left',
    color: MUTED,
    fontSize: 12,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: 1,
    borderBottom: `1px solid #ffffff11`,
  }

  const cellStyle = {
    padding: '14px 16px',
    borderBottom: `1px solid #ffffff08`,
    color: TEXT,
    fontSize: 14,
    verticalAlign: 'middle',
  }

  return (
    <div style={{ background: BG, minHeight: '100vh', color: TEXT }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, color: TEXT }}>👥 Users</h1>
            <p style={{ margin: '4px 0 0', color: MUTED, fontSize: 14 }}>
              Manage user access and roles
            </p>
          </div>
          <button
            onClick={load}
            style={{
              padding: '8px 16px',
              background: 'transparent',
              border: `1px solid ${ACCENT}55`,
              borderRadius: 6,
              color: ACCENT,
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            ↻ Refresh
          </button>
        </div>

        {/* Pending approval banner */}
        {pendingCount > 0 && (
          <div style={{
            padding: '12px 16px',
            background: '#2a1e00',
            border: '1px solid #ff990055',
            borderRadius: 8,
            color: '#ffb74d',
            fontSize: 14,
            marginBottom: 20,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}>
            ⏳ <strong>{pendingCount}</strong> user{pendingCount !== 1 ? 's' : ''} pending admin approval
          </div>
        )}

        {/* Error */}
        {error && (
          <div style={{
            padding: '12px 16px',
            background: '#2a0a0a',
            border: `1px solid ${ACCENT}55`,
            borderRadius: 8,
            color: ACCENT,
            fontSize: 14,
            marginBottom: 20,
          }}>
            ⚠️ {error}
          </div>
        )}

        {/* Table */}
        <div style={{ background: CARD, borderRadius: 10, border: `1px solid #ffffff11`, overflow: 'hidden' }}>
          {loading ? (
            <div style={{ padding: 48, textAlign: 'center', color: MUTED }}>Loading users…</div>
          ) : users.length === 0 && !error ? (
            <div style={{ padding: 48, textAlign: 'center', color: MUTED }}>No users found.</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#12122088' }}>
                  <th style={headStyle}>Name / Email</th>
                  <th style={headStyle}>Role</th>
                  <th style={headStyle}>Last Login</th>
                  <th style={headStyle}>Status</th>
                  <th style={{ ...headStyle, textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map(user => {
                  const isSelf = String(user.id) === String(me?.sub)
                  return (
                    <tr key={user.id} style={{
                      background: isSelf ? `${ACCENT}08` : 'transparent',
                      transition: 'background 0.1s',
                    }}>
                      {/* Name / Email */}
                      <td style={cellStyle}>
                        <div style={{ fontWeight: 600, color: isSelf ? ACCENT : TEXT }}>
                          {user.name || '—'} {isSelf && <span style={{ fontSize: 11, color: MUTED, fontWeight: 400 }}>(you)</span>}
                        </div>
                        <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{user.email}</div>
                      </td>

                      {/* Role dropdown */}
                      <td style={cellStyle}>
                        <select
                          value={user.role}
                          disabled={!!busy[`role-${user.id}`]}
                          onChange={e => withBusy(`role-${user.id}`, () => patchUserRole(user.id, e.target.value))}
                          style={{
                            background: '#0f0f1a',
                            border: '1px solid #ffffff22',
                            borderRadius: 5,
                            color: TEXT,
                            padding: '4px 8px',
                            fontSize: 13,
                            cursor: 'pointer',
                          }}
                        >
                          <option value="viewer">viewer</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>

                      {/* Last login */}
                      <td style={{ ...cellStyle, color: MUTED, fontSize: 12 }}>
                        {user.last_login
                          ? new Date(user.last_login).toLocaleString()
                          : '—'}
                      </td>

                      {/* Status badge */}
                      <td style={cellStyle}>
                        <Badge active={user.is_active} />
                      </td>

                      {/* Actions */}
                      <td style={{ ...cellStyle, textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {!user.is_active && (
                          <ActionBtn
                            color="#4caf8a"
                            disabled={!!busy[`act-${user.id}`]}
                            onClick={() => withBusy(`act-${user.id}`, () => activateUser(user.id))}
                          >
                            ✓ Activate
                          </ActionBtn>
                        )}
                        {user.is_active && !isSelf && (
                          <ActionBtn
                            color={ACCENT}
                            disabled={!!busy[`deact-${user.id}`]}
                            onClick={() => withBusy(`deact-${user.id}`, () => deactivateUser(user.id))}
                          >
                            ✕ Deactivate
                          </ActionBtn>
                        )}
                        {!user.is_active && !isSelf && (
                          <ActionBtn
                            color="#ff4444"
                            disabled={!!busy[`del-${user.id}`]}
                            onClick={() => {
                              if (window.confirm(`Delete ${user.email}? This cannot be undone.`)) {
                                withBusy(`del-${user.id}`, () => deleteUser(user.id))
                              }
                            }}
                          >
                            🗑 Delete
                          </ActionBtn>
                        )}
                        {user.is_active && isSelf && (
                          <span style={{ color: '#333', fontSize: 12 }}>—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        <div style={{ marginTop: 12, color: '#333', fontSize: 12, textAlign: 'right' }}>
          {users.length} user{users.length !== 1 ? 's' : ''} total
        </div>
      </div>
    </div>
  )
}
